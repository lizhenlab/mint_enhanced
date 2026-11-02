"""Persistent video collections and evaluation, using the existing native run scheduler."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from mint.runtime import atomic_json, digest, file_identity
from mint.inference.cache_budget import file_lock, _token
from .contracts import source_request
from .jobs import TERMINAL
from .registry import backend_spec, normalize_options

VIDEO_SUFFIXES = {'.mp4', '.mov', '.avi', '.mkv', '.m4v', '.webm'}


def collection_request(kind, items, selections, options=None):
    """One structural protocol for preflight and durable submission."""
    if kind not in {'batch', 'evaluation'}:
        raise ValueError('Unknown collection kind')
    if not isinstance(items, list) or not 1 <= len(items) <= 1000:
        raise ValueError('Choose 1–1000 videos or episodes per collection')
    if not isinstance(selections, list) or not 1 <= len(selections) <= 7:
        raise ValueError('Choose 1–7 inference variants, reserving one slot for the reference')
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) - {'algorithm', 'options', 'checkpoint', 'config'} or not selection.get('algorithm'):
            raise ValueError('Invalid collection algorithm selection')
        if selection['algorithm'] == 'reference':
            raise ValueError('Select inference algorithms; references are supplied by each sample')
    if options is not None and not isinstance(options, dict):
        raise ValueError('Collection input options must be an object')
    options = dict(options or {})
    if set(options) - {'view', 'target_fps', 'start_frame', 'max_frames', 'camera'}:
        raise ValueError('Unknown collection input options')
    normalized = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {'source', 'dataset', 'reference', 'name'}:
            raise ValueError('Invalid collection input descriptor')
        descriptor = dict(item)
        if item.get('source') is not None and (not isinstance(item['source'], (str, os.PathLike)) or not str(item['source']).strip()):
            raise ValueError('Collection source must be a video path')
        if not item.get('source') and 'dataset' not in item:
            raise ValueError('Each collection input requires a video or dataset Episode')
        if 'dataset' in item:
            dataset = item['dataset']
            if not isinstance(dataset, dict) or not dataset:
                raise ValueError('Dataset selection must be a nonempty object')
            dataset = dict(dataset)
            for flag in ('attach_gt', 'use_calibration'):
                if flag in dataset and not isinstance(dataset[flag], bool):
                    raise ValueError(flag + ' must be a boolean')
            if item.get('reference'):
                raise ValueError('Choose dataset annotations or an external reference, not both')
            if kind == 'evaluation':
                dataset['attach_gt'] = True
            descriptor['dataset'] = dataset
        if 'reference' in item:
            reference = item['reference']
            if not isinstance(reference, dict) or set(reference) - {'path', 'format', 'declared_source', 'frame_map'}:
                raise ValueError('Invalid collection reference descriptor')
            if reference.get('format') != 'normalized_npz' or not str(reference.get('declared_source', '')).strip():
                raise ValueError('Evaluation references require normalized geometry and a declared source')
            mapping = reference.get('frame_map')
            if not isinstance(mapping, list) or not mapping or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in mapping) or any(b <= a for a, b in zip(mapping, mapping[1:])):
                raise ValueError('Reference frame_map must contain increasing nonnegative sample positions')
            if not isinstance(reference.get('path'), str) or not reference['path'].strip():
                raise ValueError('Reference file path is required')
            descriptor['reference'] = dict(reference)
        normalized.append(descriptor)
    return normalized, options


def _reference_identity(path):
    before = file_identity(path)
    if before.get('missing'):
        return before
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(2 * 2**20), b''):
            checksum.update(block)
    if file_identity(path) != before:
        raise ValueError('Reference annotations changed during validation')
    return {**before, 'sha256': checksum.hexdigest()}


def _reference_matches(path, expected):
    # Previously published collections froze only filesystem identity. Keep
    # those protocols readable without silently rewriting their provenance.
    return (_reference_identity(path) if 'sha256' in expected else file_identity(path)) == expected


def validate_collection_reference(reference, frames=None, *, manager=None):
    """Reject malformed GT before publishing any inference child."""
    import numpy as np
    from .contracts import validate_geometry
    path = Path(reference['path']).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Reference file is unavailable')
    from zipfile import BadZipFile
    before = _reference_identity(path)
    if manager is not None:
        from .reference_validity import assert_reference_import_current
        assert_reference_import_current(manager, {**reference, 'path': str(path), 'identity': before},
                                        checksum=before['sha256'])
    try:
        archive = np.load(path, allow_pickle=False)
        if not isinstance(archive, np.lib.npyio.NpzFile):
            raise ValueError('Reference must be a normalized NPZ archive')
        with archive:
            arrays = {key: archive[key] for key in archive.files}
        count = validate_geometry(arrays)
    except (OSError, ValueError, TypeError, EOFError, BadZipFile) as error:
        raise ValueError('Invalid normalized reference geometry: ' + str(error)) from error
    if len(reference['frame_map']) != count:
        raise ValueError('Reference frame_map length must match the normalized reference geometry')
    if frames is not None and max(reference['frame_map']) >= frames:
        raise ValueError('Reference frame_map exceeds the selected input frame range')
    if _reference_identity(path) != before:
        raise ValueError('Reference annotations changed during validation')
    return before


def scan_videos(path, recursive=False, limit=1000):
    root = Path(path).expanduser().resolve()
    limit = max(1, min(10000, int(limit)))
    if root.is_file():
        paths = [root] if root.suffix.lower() in VIDEO_SUFFIXES else []
    elif root.is_dir():
        paths = []
        stack = [root]
        visited = 0
        while stack and len(paths) <= limit:
            folder = stack.pop()
            visited += 1
            if visited > 20000:
                raise ValueError('Directory scan exceeded 20,000 folders; select a smaller root')
            try:
                entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
            except PermissionError:
                continue
            for entry in entries:
                if entry.name.startswith('.'):
                    continue
                if entry.is_file() and entry.suffix.lower() in VIDEO_SUFFIXES:
                    paths.append(entry)
                elif recursive and not entry.is_symlink() and entry.is_dir():
                    stack.append(entry)
                if len(paths) > limit:
                    break
    else:
        raise ValueError('Input directory is unavailable')
    return {'root': str(root), 'truncated': len(paths) > limit,
            'videos': [{'source': str(p.resolve()), 'name': str(p.relative_to(root)) if root.is_dir() else p.name,
                        'size_bytes': p.stat().st_size} for p in paths[:limit]]}


class CollectionManager:
    def __init__(self, manager, *, autostart=True):
        self.manager = manager
        self.root = manager.root / 'collections'
        self.root.mkdir(parents=True, exist_ok=True)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.active = set()
        with manager.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS collections (id TEXT PRIMARY KEY, payload TEXT NOT NULL, '
                       'status TEXT NOT NULL, items TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, '
                       'owner_pid INTEGER, owner_token TEXT, error TEXT)')
            for row in db.execute("SELECT id,items,owner_pid,owner_token FROM collections WHERE status='running'").fetchall():
                if _token(row['owner_pid'] or 0) != row['owner_token']:
                    items = json.loads(row['items'])
                    for item in items:
                        if item.get('status') not in TERMINAL:
                            item.update(status='queued', retry=True)
                    db.execute("UPDATE collections SET status='queued',items=?,owner_pid=NULL,owner_token=NULL WHERE id=?",
                               (json.dumps(items), row['id']))
        if autostart:
            self.thread = threading.Thread(target=self._loop, daemon=True, name='comparison-collections')
            self.thread.start()
        atexit.register(self.close)

    def create(self, *, kind='batch', items, selections, options=None, title=None, _preflight=None):
        items, options = collection_request(kind, items, selections, options)
        expected = None
        if _preflight is not None:
            if not _preflight.get('ready') or len(_preflight.get('inputs', [])) != len(items):
                raise ValueError('Collection requires a ready preflight for every input')
            expected = _preflight['inputs']
        frozen, versions = [], {}
        for selection_index, selection in enumerate(selections):
            if not isinstance(selection, dict) or set(selection) - {'algorithm', 'options', 'checkpoint', 'config'}:
                raise ValueError('Invalid collection algorithm selection')
            algorithm = selection['algorithm']
            spec = backend_spec(algorithm, **{k: selection[k] for k in ('checkpoint', 'config') if selection.get(k)})
            if spec.get('import_only') or not spec['ready']:
                raise ValueError('Select ready inference backends; references are supplied by each sample')
            normalized = normalize_options(algorithm, selection.get('options'))
            if expected and any(len(row.get('backends', [])) <= selection_index or
                                row['backends'][selection_index]['algorithm'] != algorithm or
                                row['backends'][selection_index]['version'] != spec['version'] or
                                row['backends'][selection_index]['options'] != normalized for row in expected):
                raise ValueError('输入文件或算法条件已变化，请重新预检')
            versions[algorithm + ':' + digest(selection)] = spec['version']
        for index, item in enumerate(items):
            if not isinstance(item, dict) or set(item) - {'source', 'dataset', 'reference', 'name'}:
                raise ValueError('Invalid collection input descriptor')
            descriptor = dict(item)
            if item.get('dataset'):
                from .datasets import resolve_dataset_input
                dataset = {**item['dataset']}
                resolved, context = resolve_dataset_input(dataset, source=item.get('source'), **options)
                request = source_request(**resolved)
                if any(backend_spec(s['algorithm'])['camera_required'] for s in selections) and not resolved['camera']:
                    raise ValueError('K-given evaluation requires explicit dataset or manual calibration')
                descriptor.update(dataset=dataset, source=resolved['source'], snapshot=context['identity'])
            else:
                request = source_request(item.get('source', ''), **options)
                descriptor.update(source=request['source']['path'], snapshot=request['source'])
                if kind == 'evaluation' and not item.get('reference'):
                    raise ValueError('Evaluation requires dataset annotations or an explicit reference for every sample')
                if item.get('reference'):
                    reference = dict(item['reference'])
                    reference['path'] = str(Path(reference['path']).expanduser().resolve())
                    reference['identity'] = validate_collection_reference(reference, manager=self.manager)
                    if reference['identity'].get('missing'):
                        raise ValueError('Reference file is unavailable')
                    descriptor['reference'] = reference
                if any(backend_spec(s['algorithm'])['camera_required'] for s in selections) and not options.get('camera'):
                    raise ValueError('K-given collection inputs require declared calibration')
            if expected and (request['id'] != expected[index].get('sample_id') or
                             descriptor['snapshot'] != expected[index].get('input_identity') or
                             descriptor.get('reference', {}).get('identity') != expected[index].get('reference_identity')):
                raise ValueError('输入文件或算法条件已变化，请重新预检')
            frozen.append(descriptor)
        payload = {'schema': 1, 'kind': kind, 'inputs': frozen, 'selections': selections,
                   'options': options, 'backend_versions': versions, 'protocol': 'same-input-openpose21-v1'}
        collection_id = digest(payload)
        payload.update(id=collection_id, title=str(title or ('数据集评测' if kind == 'evaluation' else '多视频多算法批次'))[:200])
        now = time.time()
        with self.manager.db() as db:
            db.execute('INSERT OR IGNORE INTO collections(id,payload,status,items,created,updated) VALUES(?,?,?,?,?,?)',
                       (collection_id, json.dumps(payload), 'queued', json.dumps([{'status': 'queued'} for _ in frozen]), now, now))
        return self.get(collection_id)

    def _row(self, collection_id):
        if len(str(collection_id)) != 64 or any(c not in '0123456789abcdef' for c in str(collection_id)):
            raise KeyError('Invalid collection ID')
        with self.manager.db() as db:
            row = db.execute('SELECT * FROM collections WHERE id=?', (collection_id,)).fetchone()
        if row is None:
            raise KeyError('Collection not found')
        return dict(row)

    def get(self, collection_id):
        row = self._row(collection_id)
        payload = json.loads(row.pop('payload'))
        items = json.loads(row['items'])
        done = sum(item.get('status') in TERMINAL for item in items)
        return {**row, **payload, 'items': items, 'done': done, 'total': len(items),
                'completed': sum(item.get('status') == 'completed' for item in items),
                'counts': {status: sum(item.get('status') == status for item in items)
                           for status in ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled', 'interrupted')},
                'report_available': (self.root / collection_id / 'report.json').is_file()}

    def history(self, limit=100):
        with self.manager.db() as db:
            rows = (db.execute('SELECT id FROM collections ORDER BY created DESC').fetchall() if limit is None else
                    db.execute('SELECT id FROM collections ORDER BY created DESC LIMIT ?', (max(1, min(200, int(limit))),)).fetchall())
        return [self.get(row['id']) for row in rows]

    def _set(self, collection_id, **values):
        allowed = {'status', 'items', 'owner_pid', 'owner_token', 'error'}
        if set(values) - allowed:
            raise ValueError('Invalid collection state update')
        if 'items' in values:
            values['items'] = json.dumps(values['items'])
        values['updated'] = time.time()
        with self.manager.db() as db:
            db.execute('UPDATE collections SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', (*values.values(), collection_id))

    def cancel(self, collection_id):
        self.get(collection_id)
        with self.manager.db() as db:
            db.execute("UPDATE collections SET status='cancelling',updated=? WHERE id=? AND status NOT IN ('completed','partial','failed','cancelled','interrupted')",
                       (time.time(), collection_id))
        return self.get(collection_id)

    def resume(self, collection_id):
        self.get(collection_id)
        directory = self.root / collection_id
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with file_lock(directory / '.execution.lock', blocking=False):
                return self._resume_locked(collection_id)
        except BlockingIOError:
            return self.get(collection_id)

    def _resume_locked(self, collection_id):
        value = self.get(collection_id)
        if value['status'] in {'queued', 'running', 'cancelling'}:
            return value
        if all(item.get('status') == 'completed' for item in value['items']):
            return value
        for selection in value['selections']:
            spec = backend_spec(selection['algorithm'], **{k: selection[k] for k in ('checkpoint', 'config') if selection.get(k)})
            if value['backend_versions'][selection['algorithm'] + ':' + digest(selection)] != spec['version']:
                raise ValueError('Algorithm version changed; create a new collection to preserve the evaluation protocol')
        items = value['items']
        for item in items:
            if item.get('status') != 'completed':
                item['retry'] = True
                item['status'] = 'queued'
                item.pop('error', None)
                item.pop('errors', None)
        (self.root / collection_id / 'report.json').unlink(missing_ok=True)
        self._set(collection_id, status='queued', items=items, error=None)
        return self.get(collection_id)

    def _status(self, collection_id):
        with self.manager.db() as db:
            return db.execute('SELECT status FROM collections WHERE id=?', (collection_id,)).fetchone()['status']

    def _check_versions(self, value):
        for selection in value['selections']:
            spec = backend_spec(selection['algorithm'], **{k: selection[k] for k in ('checkpoint', 'config') if selection.get(k)})
            if spec['version'] != value['backend_versions'][selection['algorithm'] + ':' + digest(selection)]:
                raise ValueError('Algorithm version changed after collection submission; create a new collection')

    def _suspend(self, collection_id, items):
        for item in items:
            if item.get('status') not in TERMINAL:
                item.update(status='queued', retry=True)
        with self.manager.db() as db:
            db.execute("UPDATE collections SET status=CASE WHEN status='cancelling' THEN 'cancelling' ELSE 'queued' END,items=?,owner_pid=NULL,owner_token=NULL,updated=? WHERE id=?",
                       (json.dumps(items), time.time(), collection_id))

    def _loop(self):
        while not self.stop.wait(.5):
            with self.manager.db() as db:
                rows = db.execute("SELECT id FROM collections WHERE status IN ('queued','cancelling') ORDER BY created LIMIT 20").fetchall()
            for row in rows:
                with self.lock:
                    if len(self.active) >= 2 or row['id'] in self.active:
                        continue
                    self.active.add(row['id'])
                threading.Thread(target=self._execute, args=(row['id'],), daemon=True).start()

    def _cancel_children(self, value):
        for item in value['items']:
            if not item.get('comparison_id'):
                continue
            comparison = self.manager.comparison(item['comparison_id'])
            for run in comparison['runs']:
                if run['id'] in comparison.get('created_run_ids', []) and run['status'] not in TERMINAL:
                    self.manager.cancel_exclusive(run['id'], comparison['id'])

    def _finish(self, collection_id, status, items, error=None):
        """Publish one terminal snapshot without losing a concurrent cancel."""
        value = self.get(collection_id)
        if value['status'] == 'cancelling':
            status = 'cancelled'
        if status == 'cancelled':
            items = [{**item, 'status': 'cancelled'} if item.get('status') not in TERMINAL else dict(item) for item in items]
        value.update(status=status, items=items)
        # Large GT/geometry comparisons can take seconds. Keep the shared job
        # database writable while computing them; reconcile cancellation only
        # when publishing the finished report and terminal state together.
        if value['kind'] == 'evaluation':
            from .evaluation import collection_report
            report = collection_report(self.manager, value)
        else:
            report = {'collection': value, 'scope': 'Batch result index; numerical files remain in immutable run directories'}
        with self.manager.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT status FROM collections WHERE id=?', (collection_id,)).fetchone()['status']
            if current == 'cancelling':
                status = 'cancelled'
            if status == 'cancelled':
                items = [{**item, 'status': 'cancelled'} if item.get('status') not in TERMINAL else dict(item) for item in items]
            value.update(status=status, items=items, done=sum(item.get('status') in TERMINAL for item in items),
                         completed=sum(item.get('status') == 'completed' for item in items), error=error,
                         owner_pid=None, owner_token=None, report_available=True,
                         counts={key: sum(item.get('status') == key for item in items)
                                 for key in ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled', 'interrupted')})
            if value['kind'] == 'evaluation':
                report['status'] = status
                for sequence in report.get('sequences', []):
                    if 0 <= sequence.get('index', -1) < len(items):
                        sequence['status'] = items[sequence['index']]['status']
            atomic_json(self.root / collection_id / 'report.json', report)
            db.execute('UPDATE collections SET status=?,items=?,error=?,owner_pid=NULL,owner_token=NULL,updated=? WHERE id=?',
                       (status, json.dumps(items), error, time.time(), collection_id))

    def _finish_cancel(self, collection_id, items):
        self._cancel_children(self.get(collection_id))
        self._finish(collection_id, 'cancelled', items)

    def _matching_predictions(self, value, comparison):
        expected = {digest({'algorithm': selection['algorithm'],
                            'options': normalize_options(selection['algorithm'], selection.get('options')),
                            'version': value['backend_versions'][selection['algorithm'] + ':' + digest(selection)]})
                    for selection in value['selections']}
        matched, seen = [], set()
        for run in comparison['runs']:
            if run['algorithm'] == 'reference' or run.get('imported'):
                continue
            request = json.loads(self.manager.row(run['id'])['payload'])
            key = digest({'algorithm': run['algorithm'], 'options': run['options'], 'version': request['backend_version']})
            if key in expected:
                seen.add(key); matched.append(run)
        if seen != expected:
            raise ValueError('Recorded comparison no longer contains the frozen inference variants')
        return matched

    def _execute(self, collection_id):
        directory = self.root / collection_id
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with file_lock(directory / '.execution.lock', blocking=False):
                value = self.get(collection_id)
                if value['status'] == 'cancelling':
                    self._finish_cancel(collection_id, value['items'])
                    return
                with self.manager.db() as db:
                    claimed = db.execute("UPDATE collections SET status='running',owner_pid=?,owner_token=?,updated=? WHERE id=? AND status='queued'",
                                         (os.getpid(), _token(os.getpid()), time.time(), collection_id)).rowcount
                if not claimed:
                    if self._status(collection_id) == 'cancelling':
                        self._finish_cancel(collection_id, value['items'])
                    return
                self._check_versions(value)
                items = value['items']
                for index, descriptor in enumerate(value['inputs']):
                    if self.stop.is_set():
                        self._suspend(collection_id, items)
                        return
                    if self._status(collection_id) == 'cancelling':
                        self._finish_cancel(collection_id, items)
                        return
                    if items[index].get('status') in TERMINAL and not items[index].get('retry'):
                        continue
                    self._check_versions(value)
                    try:
                        options = value['options']
                        if descriptor.get('dataset'):
                            from .datasets import resolve_dataset_input
                            resolved, context = resolve_dataset_input(descriptor['dataset'], source=descriptor['source'], **options)
                            request = source_request(**resolved)
                            current = context['identity']
                        else:
                            request = source_request(descriptor['source'], **options)
                            current = request['source']
                        if current != descriptor['snapshot']:
                            raise ValueError('Collection input changed; create a new collection')
                        if descriptor.get('reference'):
                            from .reference_validity import assert_reference_import_current
                            assert_reference_import_current(self.manager, descriptor['reference'])
                            if not _reference_matches(descriptor['reference']['path'], descriptor['reference']['identity']):
                                raise ValueError('Reference annotations changed')
                        if items[index].get('comparison_id'):
                            comparison = self.manager.comparison(items[index]['comparison_id'])
                            if comparison['sample_id'] != request['id']:
                                raise ValueError('Recorded comparison input no longer matches the frozen batch')
                            predictions = self._matching_predictions(value, comparison)
                            tracked = items[index].get('run_ids') or [run['id'] for run in predictions] + [run['id'] for run in comparison['runs'] if run['algorithm'] == 'reference']
                        else:
                            comparison = self.manager.create(descriptor['source'], value['selections'], execution={'priority':'batch'}, **options,
                                dataset=descriptor.get('dataset'), title=descriptor.get('name') or Path(descriptor['source']).name,
                                comparison_key=f'{collection_id}:{index}')
                            tracked = comparison['run_ids'][:]
                        items[index]['run_ids'] = list(dict.fromkeys(tracked))
                        items[index].update(comparison_id=comparison['id'], status='running')
                        self._set(collection_id, items=items)
                        if descriptor.get('reference'):
                            entry = dict(descriptor['reference'])
                            expected = entry.pop('identity')
                            if not _reference_matches(entry['path'], expected):
                                raise ValueError('Reference annotations changed')
                            if expected.get('sha256'):
                                entry['declared_source'] += '\nMINT frozen external reference SHA256: ' + expected['sha256']
                            reference = self.manager.create(descriptor['source'], [{'algorithm': 'reference'}], **options,
                                import_entry=entry, comparison_key=f'{collection_id}:{index}:reference')
                            self.manager.attach(comparison['id'], reference['run_ids'][0])
                            if reference['run_ids'][0] not in items[index]['run_ids']:
                                items[index]['run_ids'].append(reference['run_ids'][0])
                            comparison = self.manager.comparison(comparison['id'])
                        retry = items[index].pop('retry', False)
                        items[index].update(comparison_id=comparison['id'], status='running')
                        self._set(collection_id, items=items)
                        if retry:
                            for run in comparison['runs']:
                                if run['id'] in items[index]['run_ids'] and run['status'] in {'failed', 'cancelled', 'interrupted', 'partial'}:
                                    self.manager.resume(run['id'])
                        while True:
                            if self.stop.is_set():
                                self._suspend(collection_id, items)
                                return
                            if self._status(collection_id) == 'cancelling':
                                self._finish_cancel(collection_id, items)
                                return
                            comparison = self.manager.comparison(comparison['id'])
                            runs = list({run['id']: run for run in comparison['runs'] if run['id'] in items[index]['run_ids']}.values())
                            if len(runs) != len(items[index]['run_ids']):
                                raise ValueError('A frozen batch run is unavailable from its comparison')
                            if all(run['status'] in TERMINAL for run in runs):
                                break
                            self.stop.wait(1)
                        statuses = [run['status'] for run in runs]
                        items[index]['status'] = 'completed' if all(s == 'completed' for s in statuses) else 'partial' if any(run['algorithm'] != 'reference' and run['result'] for run in runs) else 'failed'
                        items[index]['errors'] = [run['error'] for run in runs if run.get('error')]
                    except Exception as error:
                        items[index].update(status='failed', error=str(error))
                        if items[index].get('comparison_id'):
                            # A reference/attachment error after child creation
                            # must not leave new, exclusive inference work orphaned.
                            self._cancel_children({'items': [items[index]]})
                    self._set(collection_id, items=items)
                state = 'completed' if all(item['status'] == 'completed' for item in items) else 'partial' if any(item['status'] in {'completed', 'partial'} for item in items) else 'failed'
                self._finish(collection_id, state, items)
        except BlockingIOError:
            pass
        except Exception as error:
            value = self.get(collection_id)
            items = [{**item, 'status': 'failed', 'error': str(error)} if item.get('status') not in TERMINAL else item
                     for item in value['items']]
            try:
                self._finish(collection_id, 'failed', items, error=str(error))
            except Exception:
                self._set(collection_id, status='failed', items=items, error=str(error), owner_pid=None, owner_token=None)
        finally:
            with self.lock:
                self.active.discard(collection_id)

    def report(self, collection_id):
        self.get(collection_id)
        path = self.root / collection_id / 'report.json'
        if not path.is_file():
            raise ValueError('Report is available after collection processing completes')
        return json.loads(path.read_text())

    def close(self):
        self.stop.set()
