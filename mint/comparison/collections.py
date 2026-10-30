"""Persistent video collections and evaluation, using the existing native run scheduler."""
from __future__ import annotations

import atexit
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

    def create(self, *, kind='batch', items, selections, options=None, title=None):
        if kind not in {'batch', 'evaluation'}:
            raise ValueError('Unknown collection kind')
        if not isinstance(items, list) or not 1 <= len(items) <= 1000:
            raise ValueError('Choose 1–1000 videos or episodes per collection')
        if not isinstance(selections, list) or not 1 <= len(selections) <= 7:
            raise ValueError('Choose 1–7 inference variants, reserving one slot for the reference')
        options = dict(options or {})
        if set(options) - {'view', 'target_fps', 'start_frame', 'max_frames', 'camera'}:
            raise ValueError('Unknown collection input options')
        frozen, versions = [], {}
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) - {'algorithm', 'options', 'checkpoint', 'config'}:
                raise ValueError('Invalid collection algorithm selection')
            algorithm = selection['algorithm']
            spec = backend_spec(algorithm, **{k: selection[k] for k in ('checkpoint', 'config') if selection.get(k)})
            if spec.get('import_only') or not spec['ready']:
                raise ValueError('Select ready inference backends; references are supplied by each sample')
            normalize_options(algorithm, selection.get('options'))
            versions[algorithm + ':' + digest(selection)] = spec['version']
        for item in items:
            if not isinstance(item, dict) or set(item) - {'source', 'dataset', 'reference', 'name'}:
                raise ValueError('Invalid collection input descriptor')
            descriptor = dict(item)
            if item.get('dataset'):
                from .datasets import resolve_dataset_input
                dataset = {**item['dataset']}
                if kind == 'evaluation':
                    dataset['attach_gt'] = True
                resolved, context = resolve_dataset_input(dataset, source=item.get('source'), **options)
                source_request(**resolved)
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
                    if reference.get('format') != 'normalized_npz' or not reference.get('declared_source'):
                        raise ValueError('Evaluation references require normalized geometry and a declared source')
                    reference['identity'] = file_identity(reference['path'])
                    if reference['identity'].get('missing'):
                        raise ValueError('Reference file is unavailable')
                    descriptor['reference'] = reference
                if any(backend_spec(s['algorithm'])['camera_required'] for s in selections) and not options.get('camera'):
                    raise ValueError('K-given collection inputs require declared calibration')
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
        value = self.get(collection_id)
        if value['status'] not in TERMINAL:
            self._set(collection_id, status='cancelling')
        return self.get(collection_id)

    def resume(self, collection_id):
        value = self.get(collection_id)
        if value['status'] in {'queued', 'running', 'cancelling'}:
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
        self._set(collection_id, status='queued', items=items, owner_pid=None, owner_token=None)

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

    def _execute(self, collection_id):
        directory = self.root / collection_id
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with file_lock(directory / '.execution.lock', blocking=False):
                value = self.get(collection_id)
                if value['status'] == 'cancelling':
                    self._cancel_children(value)
                    self._set(collection_id, status='cancelled')
                    return
                self._set(collection_id, status='running', owner_pid=os.getpid(), owner_token=_token(os.getpid()))
                self._check_versions(value)
                items = value['items']
                for index, descriptor in enumerate(value['inputs']):
                    if self.stop.is_set():
                        self._suspend(collection_id, items)
                        return
                    if self._status(collection_id) == 'cancelling':
                        self._cancel_children(self.get(collection_id))
                        self._set(collection_id, status='cancelled')
                        return
                    if items[index].get('status') in TERMINAL and not items[index].get('retry'):
                        continue
                    self._check_versions(value)
                    try:
                        options = value['options']
                        if descriptor.get('dataset'):
                            from .datasets import select_episode, annotation_identity
                            root, info, key, episode, capability = select_episode(descriptor['dataset'])
                            current = annotation_identity(root, episode, key)
                        else:
                            current = file_identity(descriptor['source'])
                        if current != descriptor['snapshot']:
                            raise ValueError('Collection input changed; create a new collection')
                        comparison = self.manager.create(descriptor['source'], value['selections'], execution={'priority':'batch'}, **options,
                            dataset=descriptor.get('dataset'), title=descriptor.get('name') or Path(descriptor['source']).name,
                            comparison_key=f'{collection_id}:{index}')
                        if descriptor.get('reference'):
                            entry = dict(descriptor['reference'])
                            expected = entry.pop('identity')
                            if file_identity(entry['path']) != expected:
                                raise ValueError('Reference annotations changed')
                            reference = self.manager.create(descriptor['source'], [{'algorithm': 'reference'}], **options,
                                import_entry=entry, comparison_key=f'{collection_id}:{index}:reference')
                            self.manager.attach(comparison['id'], reference['run_ids'][0])
                            comparison = self.manager.comparison(comparison['id'])
                        retry = items[index].pop('retry', False)
                        items[index].update(comparison_id=comparison['id'], status='running')
                        self._set(collection_id, items=items)
                        if retry:
                            for run in comparison['runs']:
                                if run['status'] in {'failed', 'cancelled', 'interrupted', 'partial'}:
                                    self.manager.resume(run['id'])
                        while True:
                            if self.stop.is_set():
                                self._suspend(collection_id, items)
                                return
                            if self._status(collection_id) == 'cancelling':
                                self._cancel_children(self.get(collection_id))
                                self._set(collection_id, status='cancelled', items=items)
                                return
                            comparison = self.manager.comparison(comparison['id'])
                            if all(run['status'] in TERMINAL for run in comparison['runs']):
                                break
                            self.stop.wait(1)
                        statuses = [run['status'] for run in comparison['runs']]
                        items[index]['status'] = 'completed' if all(s == 'completed' for s in statuses) else 'partial' if any(run['result'] for run in comparison['runs']) else 'failed'
                        items[index]['errors'] = [run['error'] for run in comparison['runs'] if run.get('error')]
                    except Exception as error:
                        items[index].update(status='failed', error=str(error))
                    self._set(collection_id, items=items)
                state = 'completed' if all(item['status'] == 'completed' for item in items) else 'partial' if any(item.get('comparison_id') for item in items) else 'failed'
                if value['kind'] == 'evaluation':
                    from .evaluation import collection_report
                    atomic_json(directory / 'report.json', collection_report(self.manager, {**self.get(collection_id), 'status': state}))
                else:
                    atomic_json(directory / 'report.json', {'collection': {**self.get(collection_id), 'status': state}, 'scope': 'Batch result index; numerical files remain in immutable run directories'})
                self._set(collection_id, status=state, items=items)
        except BlockingIOError:
            pass
        except Exception as error:
            self._set(collection_id, status='failed', error=str(error))
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
