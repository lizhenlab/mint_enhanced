"""Durable run identities and owned workers; no model is loaded by the web process."""
from __future__ import annotations

import atexit
import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

from mint.runtime import atomic_json, digest, file_identity
from mint.inference.cache_budget import _token, file_lock
from mint.inference.gpu_scheduler import GpuLease, NvidiaSmiProbe
from .contracts import source_request, load_result
from .registry import ALGORITHMS, PROJECT, backend_spec, normalize_options

TERMINAL = {'completed', 'partial', 'failed', 'cancelled', 'interrupted'}


def default_root():
    return Path(os.environ.get('MINT_COMPARISON_DIR') or PROJECT / '.cache/comparison').resolve()


def process_matches(pid, token, request_path):
    if not pid or not token or _token(pid) != token:
        return False
    try:
        proc = Path('/proc') / str(pid)
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        command = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode()
        return fields[0] != 'Z' and proc.stat().st_uid == os.getuid() and 'mint.comparison.worker' in command and str(request_path) in command
    except OSError:
        return False


class ComparisonManager:
    def __init__(self, directory=None, *, autostart=True):
        self.root = Path(directory or default_root())
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.root / 'jobs.sqlite'
        self.owner = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._threads = {}
        self._import_threads = set()
        self._stopping = threading.Event()
        self._probe = NvidiaSmiProbe()
        self._last_probe, self._gpu_samples = 0, {}
        self._maximum = max(1, min(8, int(os.environ.get('MINT_COMPARISON_MAX_ACTIVE', '3'))))
        self._maximum_imports = max(1, min(4, int(os.environ.get('MINT_COMPARISON_MAX_IMPORTS', '2'))))
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, '
                       'created REAL NOT NULL, updated REAL NOT NULL, worker_pid INTEGER, worker_token TEXT, owner TEXT, '
                       'owner_pid INTEGER, owner_token TEXT, error TEXT)')
            if 'priority' not in {r[1] for r in db.execute('PRAGMA table_info(runs)')}:
                db.execute('ALTER TABLE runs ADD COLUMN priority INTEGER NOT NULL DEFAULT 1')
            db.execute('CREATE INDEX IF NOT EXISTS run_admission ON runs(status,priority,created)')
            db.execute('CREATE TABLE IF NOT EXISTS comparisons (id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL)')
        self._recover()
        if autostart:
            self._coordinator = threading.Thread(target=self._loop, name='comparison-coordinator', daemon=True)
            self._coordinator.start()
        atexit.register(self.close)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    def _recover(self):
        with self.db() as db:
            records = db.execute("SELECT * FROM runs WHERE status NOT IN ('completed','partial','failed','cancelled','interrupted')").fetchall()
            for row in records:
                if row['status'] == 'queued':
                    continue
                if row['owner_pid'] and _token(row['owner_pid']) == row['owner_token']:
                    continue
                request_path = self.root / 'runs' / row['id'] / 'request.json'
                exit_path = request_path.parent / 'exit.json'
                if exit_path.exists():
                    status = json.loads(exit_path.read_text())
                    db.execute('UPDATE runs SET status=?,error=?,updated=? WHERE id=?',
                               (status['status'], status.get('error'), time.time(), row['id']))
                elif process_matches(row['worker_pid'], row['worker_token'], request_path):
                    # The child inherited its GPU lease FD; it remains exclusive after its parent exits.
                    db.execute('UPDATE runs SET owner=?,owner_pid=?,owner_token=? WHERE id=?',
                               (self.owner, os.getpid(), _token(os.getpid()), row['id']))
                    thread = threading.Thread(target=self._watch_recovered, args=(dict(row),), daemon=True)
                    self._threads[row['id']] = thread; thread.start()
                    if json.loads(row['payload']).get('import'):
                        self._import_threads.add(row['id'])
                else:
                    db.execute("UPDATE runs SET status='interrupted',error=?,updated=? WHERE id=?",
                               ('Previous owner exited; resume will reuse complete native stages', time.time(), row['id']))

    def _watch_recovered(self, row):
        path = self.root / 'runs' / row['id'] / 'request.json'
        while not self._stopping.wait(.5) and process_matches(row['worker_pid'], row['worker_token'], path):
            pass
        if self._stopping.is_set():
            return
        self._finish_from_disk(row['id'])

    def _set(self, run_id, status=None, **values):
        if status is not None:
            values['status'] = status
        values['updated'] = time.time()
        allowed = {'status', 'updated', 'worker_pid', 'worker_token', 'owner', 'owner_pid', 'owner_token', 'error'}
        if not set(values) <= allowed:
            raise ValueError('Invalid job state fields')
        with self.db() as db:
            db.execute('UPDATE runs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), run_id])

    def create(self, source, selections, *, view='full', target_fps=0, max_frames=None, start_frame=0,
               camera=None, title=None, import_entry=None, dataset=None, comparison_key=None, execution=None):
        from .execution import execution_options, compatible_request, PRIORITIES
        intent = execution_options(execution)
        reuse = {}
        dataset_context = None
        if dataset:
            if import_entry:
                raise ValueError('Dataset references and external imports are separate operations')
            from .datasets import resolve_dataset_input
            resolved, dataset_context = resolve_dataset_input(dataset, source=source, view=view,
                target_fps=target_fps, max_frames=max_frames, start_frame=start_frame, camera=camera)
            source, view, target_fps, max_frames, start_frame, camera = (resolved[k] for k in
                ('source', 'view', 'target_fps', 'max_frames', 'start_frame', 'camera'))
            if dataset.get('attach_gt'):
                selections = [dict(item) for item in selections if item.get('algorithm') != 'reference'] + [{'algorithm': 'reference'}]
        sample = source_request(source, view=view, target_fps=target_fps, max_frames=max_frames,
                                start_frame=start_frame, camera=camera)
        if not isinstance(selections, list) or not 1 <= len(selections) <= 8:
            raise ValueError('Choose between one and eight algorithm variants')
        requests = []
        for selection in selections:
            if not isinstance(selection, dict):
                raise ValueError('Invalid algorithm selection')
            algorithm = selection.get('algorithm')
            if set(selection) - {'algorithm', 'options', 'checkpoint', 'config'}:
                raise ValueError('Unknown algorithm selection fields')
            custom = {key: selection[key] for key in ('checkpoint', 'config') if selection.get(key)}
            spec = backend_spec(algorithm, **custom) if custom else backend_spec(algorithm)
            dataset_reference = algorithm == 'reference' and dataset_context and dataset.get('attach_gt')
            if spec.get('import_only') and not (import_entry or dataset_reference):
                raise ValueError('Reference requires an explicit normalized geometry import and provenance')
            if not spec['ready']:
                raise ValueError(spec['label'] + ' dependencies unavailable: ' + ', '.join(spec['missing']))
            if spec['camera_required'] and not camera:
                raise ValueError('ACE K-given requires explicit calibration')
            options = normalize_options(algorithm, selection.get('options'))
            identity = {'schema': 1, 'sample_id': sample['id'], 'algorithm': algorithm,
                        'backend_version': spec['version'], 'options': options}
            if dataset_reference:
                identity['import'] = {'format': 'lerobot_reference', 'dataset_context': dataset_context,
                    'path': dataset_context['identity']['parquet']['path'],
                    'identity': dataset_context['identity']['parquet']}
            elif import_entry:
                path = Path(import_entry['path']).expanduser().resolve()
                if not path.is_file():
                    raise ValueError('Native import file not found')
                form = import_entry['format']
                formats = {'reference': {'normalized_npz'}, 'mint': {'mint_npz', 'normalized_npz'}, 'hawor_full': {'hawor_world_npz', 'normalized_npz'},
                           'hawor_camera': {'normalized_npz'}, 'ace_kfree': {'ace_pickle', 'normalized_npz'},
                           'ace_kgiven': {'ace_pickle', 'normalized_npz'}}
                if form not in formats.get(algorithm, set()):
                    raise ValueError('Import format does not match the selected algorithm environment')
                mapping = import_entry.get('frame_map')
                if not isinstance(mapping, list) or not mapping:
                    raise ValueError('Import requires an explicit frame_map of sample-relative positions')
                if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in mapping) or any(b <= a for a, b in zip(mapping, mapping[1:])):
                    raise ValueError('Import frame_map must contain increasing nonnegative integer positions')
                entry = {'path': str(path), 'identity': file_identity(path), 'format': form, 'frame_map': mapping,
                         'declared_source': str(import_entry.get('declared_source') or 'Explicit server-native result import')}
                if form == 'mint_npz':
                    frame = import_entry.get('hand_frame', 'camera')
                    if frame not in {'camera', 'world'}:
                        raise ValueError('Declare camera or world for imported MINT hand parameters')
                    entry['hand_frame'] = frame
                if algorithm == 'reference':
                    if not str(import_entry.get('declared_source', '')).strip():
                        raise ValueError('Declare the GT origin, coordinate frame and metric units')
                    entry['reference_role'] = 'user_declared_ground_truth'
                if form == 'hawor_world_npz':
                    grid = import_entry.get('image_size')
                    if not isinstance(grid, list) or len(grid) != 2 or any(not isinstance(v, int) or v < 1 for v in grid):
                        raise ValueError('HaWoR import requires explicit image_size [width,height] of its native camera grid')
                    entry['image_size'] = grid
                identity['import'] = entry
            if intent['cache'] == 'fresh':
                identity['fresh_nonce'] = digest({'fresh_comparison': comparison_key}) if comparison_key else uuid.uuid4().hex
            run_id = digest(identity)
            request = {**identity, 'id': run_id, 'backend': spec, 'sample_request': sample,
                       'sample_manifest': str(self.root / 'inputs' / sample['id'] / 'sample.json')}
            request['execution'] = intent
            if intent['cache'] == 'compatible' and not request.get('import'):
                with self.db() as db:
                    candidates = db.execute("SELECT id,payload FROM runs WHERE status='completed' ORDER BY updated DESC").fetchall()
                for candidate in candidates:
                    if not compatible_request(json.loads(candidate['payload']), request):
                        continue
                    try:
                        from .contracts import result_metadata
                        evidence = result_metadata(self.root / 'runs' / candidate['id'] / 'result')
                        if evidence.get('components') or evidence.get('reference_role'):
                            continue
                    except (OSError, ValueError, KeyError):
                        continue
                    reuse[candidate['id']] = {'requested_options': options, 'source_run': candidate['id'],
                        'requested_execution': intent, 'basis': 'identical source/backend/numerical options; validated immutable files',
                        'timing_scope': 'cached_result; exclude from cold performance comparisons'}
                    request['id'] = candidate['id']
                    break
            requests.append(request)
        comparison_id = uuid.uuid5(uuid.NAMESPACE_URL, str(comparison_key)).hex if comparison_key else uuid.uuid4().hex
        comparison = {'id': comparison_id, 'title': str(title or Path(source).name)[:200],
                      'sample_id': sample['id'], 'sample_request': sample, 'run_ids': [v['id'] for v in requests],
                      'execution': intent, 'reuse': reuse}
        if dataset_context:
            comparison['dataset_context'] = dataset_context
        with self.db() as db:
            previous = db.execute('SELECT payload FROM comparisons WHERE id=?', (comparison_id,)).fetchone()
            if previous:
                old = json.loads(previous['payload'])
                if old['sample_id'] != comparison['sample_id'] or old['run_ids'] != comparison['run_ids']:
                    raise ValueError('Idempotent comparison inputs changed; create a new batch')
                return self.comparison(comparison_id)
            created = []
            for request in requests:
                cursor = db.execute('INSERT OR IGNORE INTO runs(id,payload,status,created,updated,priority) VALUES(?,?,?,?,?,?)',
                                    (request['id'], json.dumps(request), 'queued', time.time(), time.time(), PRIORITIES[intent['priority']]))
                if cursor.rowcount:
                    created.append(request['id'])
                else:
                    db.execute("UPDATE runs SET priority=max(priority,?) WHERE id=? AND status='queued'",
                               (PRIORITIES[intent['priority']], request['id']))
            comparison['created_run_ids'] = created
            db.execute('INSERT INTO comparisons(id,payload,created) VALUES(?,?,?)',
                       (comparison_id, json.dumps(comparison), time.time()))
        return self.comparison(comparison_id)

    def cancel_exclusive(self, run_id, comparison_id):
        """A batch cancellation must not stop a run shared with another comparison."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            parents = db.execute('SELECT id,payload FROM comparisons WHERE id != ?', (comparison_id,)).fetchall()
            if any(run_id in json.loads(row['payload'])['run_ids'] for row in parents):
                return False
            row = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise KeyError('Run not found')
            if row['status'] not in TERMINAL:
                path = self.root / 'runs' / run_id
                path.mkdir(parents=True, exist_ok=True)
                (path / 'cancel').touch()
                db.execute('UPDATE runs SET status=?,updated=? WHERE id=?',
                           ('cancelled' if row['status'] == 'queued' else 'cancelling', time.time(), run_id))
        return True

    def row(self, run_id):
        if len(str(run_id)) != 64 or any(c not in '0123456789abcdef' for c in str(run_id)):
            raise KeyError('Invalid run ID')
        with self.db() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise KeyError('Run not found')
        return dict(row)

    def run(self, run_id, *, summary=False):
        return self._run_record(self.row(run_id), summary=summary)

    def _run_record(self, record, *, summary=False):
        row = dict(record)
        run_id = row['id']
        payload = json.loads(row.pop('payload'))
        path = self.root / 'runs' / run_id
        progress = {}
        try:
            progress = json.loads((path / 'progress.json').read_text())
        except (OSError, ValueError):
            pass
        from .observability import describe_progress
        progress = describe_progress(progress)
        result = None
        try:
            if summary:
                from .contracts import result_metadata
                result = result_metadata(path / 'result')
            else:
                result = load_result(path / 'result')[0]
            from .results import public_metadata
            result = public_metadata(result)
        except (OSError, ValueError, KeyError):
            if row['status'] in {'completed', 'partial'}:
                row.update(status='failed', error='Completed result files changed or are unavailable; resume to regenerate')
        withdrawal = payload.get('annotation_withdrawal') if payload['algorithm'] == 'reference' else None
        if withdrawal:
            result = None
            row.update(status='failed', error=withdrawal['reason'])
        return {**row, 'algorithm': payload['algorithm'], 'label': payload['backend']['label'],
                'checkpoint': payload['backend'].get('checkpoint'), 'config': payload['backend'].get('config'),
                'execution': payload.get('execution', {'priority':'batch','cache':'exact'}),
                'options': payload['options'], 'sample_id': payload['sample_id'], 'progress': progress,
                'result': result, 'imported': bool(payload.get('import')),
                **({'annotation_withdrawal': withdrawal} if withdrawal else {})}

    def comparison(self, comparison_id, *, include_sample=True):
        with self.db() as db:
            row = db.execute('SELECT payload,created FROM comparisons WHERE id=?', (comparison_id,)).fetchone()
        if row is None:
            raise KeyError('Comparison not found')
        item = json.loads(row['payload'])
        item['created'] = row['created']
        item['runs'] = [self.run(run_id) for run_id in item['run_ids']]
        for run in item['runs']:
            run['reuse'] = item.get('reuse', {}).get(run['id'])
        try:
            item['sample'] = json.loads((self.root / 'inputs' / item['sample_id'] / 'sample.json').read_text()) if include_sample else None
        except (OSError, ValueError):
            item['sample'] = None
        return item

    def history(self, limit=50, offset=0):
        with self.db() as db:
            rows = db.execute('SELECT id FROM comparisons ORDER BY created DESC LIMIT ? OFFSET ?',
                              (max(1, min(200, int(limit))), max(0, int(offset)))).fetchall()
        return [self.comparison(row['id'], include_sample=False) for row in rows]

    def resume(self, run_id):
        row = self.row(run_id)
        payload = json.loads(row['payload'])
        if payload['algorithm'] == 'reference' and payload.get('annotation_withdrawal'):
            raise ValueError('This reference was withdrawn: ' + payload['annotation_withdrawal']['reason'])
        if payload['algorithm'] == 'camera_combination':
            raise ValueError('Regenerate camera combinations from their complete source results using Generate combination')
        selection = {key: payload['backend'][key] for key in ('checkpoint', 'config') if payload['backend'].get(key)}
        if backend_spec(payload['algorithm'], **selection)['version'] != payload['backend']['version']:
            raise ValueError('Model/code version changed; submit a new comparison')
        if file_identity(payload['sample_request']['source']['path']) != payload['sample_request']['source']:
            raise ValueError('Source changed; submit a new comparison')
        current = self.run(run_id)
        if current['status'] == 'completed' and current['result']:
            return current
        if row['status'] not in TERMINAL:
            return current
        path = self.root / 'runs' / run_id
        if (path / 'result').exists():
            os.replace(path / 'result', path / ('previous-result-' + uuid.uuid4().hex))
        (path / 'cancel').unlink(missing_ok=True)
        (path / 'exit.json').unlink(missing_ok=True)
        self._set(run_id, 'queued', error=None, worker_pid=None, worker_token=None, owner=None, owner_pid=None, owner_token=None)
        return self.run(run_id)

    def cancel(self, run_id):
        row = self.row(run_id)
        path = self.root / 'runs' / run_id
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if row['status'] not in TERMINAL:
            (path / 'cancel').touch()
            self._set(run_id, 'cancelled' if row['status'] == 'queued' else 'cancelling')
        return self.run(run_id)

    def _loop(self):
        while not self._stopping.wait(.5):
            with self._lock:
                self._threads = {k: v for k, v in self._threads.items() if v.is_alive()}
                self._import_threads.intersection_update(self._threads)
                import_slots = self._maximum_imports - len(self._import_threads)
                slots = self._maximum - (len(self._threads) - len(self._import_threads))
            if slots <= 0 and import_slots <= 0:
                continue
            with self.db() as db:
                rows = db.execute("SELECT id,payload FROM runs WHERE status='queued' ORDER BY (priority + min(2,CAST((? - created)/300 AS INTEGER))) DESC,created LIMIT 128", (time.time(),)).fetchall()
                for row in rows:
                    imported = bool(json.loads(row['payload']).get('import'))
                    if (imported and import_slots <= 0) or (not imported and slots <= 0):
                        continue
                    changed = db.execute("UPDATE runs SET status='preparing',owner=?,owner_pid=?,owner_token=?,updated=? WHERE id=? AND status='queued'",
                        (self.owner, os.getpid(), _token(os.getpid()), time.time(), row['id'])).rowcount
                    if changed:
                        thread = threading.Thread(target=self._execute, args=(row['id'],), name='comparison-' + row['id'][:8], daemon=True)
                        with self._lock:
                            self._threads[row['id']] = thread
                            if imported:
                                self._import_threads.add(row['id'])
                        if imported:
                            import_slots -= 1
                        else:
                            slots -= 1
                        # Commit the claim before the execution thread opens another connection.
                        db.commit(); thread.start()

    def _acquire_gpu(self, request, cancelled):
        if request.get('import') or request.get('external_pool'):
            return [], []
        if request['algorithm'] == 'mint' and request['options']['resource_policy'] in {'shared', 'idle'}:
            return [], []  # The unchanged MINT scheduler owns the same cooperative GPU leases.
        spec, policy = request['backend'], request['options']['resource_policy']
        while not self._stopping.is_set():
            if cancelled():
                raise InterruptedError('Cancelled while queued for a GPU')
            try:
                samples = self._probe()
            except Exception as exc:
                self._write_progress(request['id'], stage='waiting_gpu', detail=str(exc))
                self._stopping.wait(2); continue
            for gpu, sample in sorted(samples.items(), key=lambda item: (-item[1].free_mib, item[0])):
                admissible = sample.free_mib >= spec['min_free_mib'] and sample.utilization <= (35 if policy == 'shared' else 10)
                if policy in {'idle', 'dedicated'} and sample.foreign_pids:
                    admissible = False
                if not admissible:
                    continue
                lease = GpuLease(gpu)
                if lease.acquire():
                    return [gpu], [lease]
            self._write_progress(request['id'], stage='waiting_gpu', detail='Waiting for safe headroom and a cooperative GPU lease')
            self._stopping.wait(2)
        raise InterruptedError('Coordinator is stopping')

    def _write_progress(self, run_id, **values):
        directory = self.root / 'runs' / run_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_json(directory / 'progress.json', {**values, 'updated_at': time.time()})

    def _execute(self, run_id):
        row = self.row(run_id)
        request = json.loads(row['payload'])
        directory = self.root / 'runs' / run_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        cancel = lambda: (directory / 'cancel').exists() or self._stopping.is_set()
        leases, child, child_token = [], None, None
        storage = contextlib.ExitStack()
        try:
            with file_lock(directory / '.execution.lock', blocking=False):
                from .prepare import prepare_sample
                if cancel():
                    raise InterruptedError('Cancelled before preparation')
                preparing_started = time.perf_counter()
                sample = prepare_sample(request['sample_request'], self.root / 'inputs' / request['sample_id'],
                    progress=lambda **kw: self._write_progress(run_id, **kw), cancel=cancel)
                request['orchestration'] = {'preparation_seconds': time.perf_counter() - preparing_started,
                    'queue_seconds': max(0., row['updated'] - row['created']),
                    'scope': 'Wall time; shared preparation waiting is included'}
                if request.get('import', {}).get('frame_map') and max(request['import']['frame_map']) >= sample['frames']:
                    raise ValueError('Import frame map escapes the prepared sample')
                from .storage import DurableRunBudget, estimate_native_bytes
                budget = DurableRunBudget(self.root / 'runs')
                try:
                    storage.enter_context(budget.reserve(directory / '.native-work', estimate_native_bytes(request, sample)))
                except OSError as exc:
                    raise OSError('Comparison native/result storage quota or free-space reserve exceeded; '
                                  'archive selected old runs or increase MINT_COMPARISON_RUN_MAX_GIB') from exc
                request['external_pool'] = not request.get('import') and request['algorithm'] in {'ace_kfree','ace_kgiven','hawor_full','hawor_camera'}
                uuids, leases = self._acquire_gpu(request, cancel)
                if cancel():
                    raise InterruptedError('Cancelled before worker launch')
                dynamic = request['algorithm'] == 'mint' and request['options']['resource_policy'] in {'shared', 'idle'} and not request.get('import')
                request.update(gpu_uuids=uuids, dynamic_gpus=dynamic, parent_pid=os.getpid())
                atomic_json(directory / 'request.json', request)
                env = os.environ.copy()
                env.update(PYTHONPATH=str(PROJECT), PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
                    OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', MINT_MANO_DEVICE='cpu',
                    MINT_GPU_MAX_WORKERS=str(request['options']['max_gpus']),
                    MINT_TASK_PRIORITY={0:'background',1:'batch',2:'interactive'}.get(row.get('priority', 1), 'batch'))
                if not dynamic:
                    env['CUDA_VISIBLE_DEVICES'] = ','.join(uuids)
                command = [request['backend']['python'], '-m', 'mint.comparison.worker', '--request', str(directory / 'request.json')]
                if request['algorithm'].startswith('hawor'):
                    setup = Path(request['backend']['root']) / 'deploy/env.sh'
                    if setup.is_file():
                        command = ['bash', '-c', 'source "$1"; shift; exec "$@"', 'comparison-worker', str(setup), *command]
                self._write_progress(run_id, stage='launching', gpu_uuids=uuids, dynamic_gpus=dynamic)
                with (directory / 'worker.log').open('ab', buffering=0) as log:
                    # Resolve the adapter from this release before entering the native repository.
                    child = subprocess.Popen(command, cwd=PROJECT, env=env, stdin=subprocess.DEVNULL,
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
                        pass_fds=tuple(lease.file.fileno() for lease in leases))
                child_token = _token(child.pid)
                self._set(run_id, 'running', worker_pid=child.pid, worker_token=child_token)
                cancelled_at = None
                deadline = time.monotonic() + float(os.environ.get('MINT_COMPARISON_JOB_TIMEOUT_SECONDS', '86400'))
                while child.poll() is None:
                    if cancel() or time.monotonic() > deadline:
                        if time.monotonic() > deadline:
                            (directory / 'cancel').touch()
                        if cancelled_at is None:
                            cancelled_at = time.monotonic()
                        if time.monotonic() - cancelled_at > 5 and process_matches(child.pid, child_token, directory / 'request.json'):
                            os.kill(child.pid, signal.SIGTERM)
                        if time.monotonic() - cancelled_at > 15:
                            break
                    time.sleep(.25)
                from mint.service import _reap_session
                _reap_session(child, child_token or '0', grace=5)
                child.wait(timeout=10)
                self._finish_from_disk(run_id, returncode=child.returncode)
        except BaseException as exc:
            status = 'cancelled' if cancel() or isinstance(exc, InterruptedError) else 'failed'
            self._set(run_id, status, error=str(exc))
            self._write_progress(run_id, stage=status, error=str(exc))
        finally:
            if child is not None and child.poll() is None:
                from mint.service import _reap_session
                _reap_session(child, child_token or '0', grace=5)
            for lease in leases:
                lease.release()
            storage.close()

    def _finish_from_disk(self, run_id, returncode=None):
        directory = self.root / 'runs' / run_id
        try:
            result = json.loads((directory / 'exit.json').read_text())
            status = result['status']
            if status in {'completed', 'partial'}:
                load_result(directory / 'result')
            self._set(run_id, status, error=result.get('error'))
        except (OSError, ValueError, KeyError) as exc:
            cancelled = (directory / 'cancel').exists()
            self._set(run_id, 'cancelled' if cancelled else 'interrupted',
                      error=f'Worker exited without a valid complete result (exit={returncode}): {exc}')

    def combine(self, comparison_id, hand_run, camera_run):
        from .results import combine_camera
        comparison = self.comparison(comparison_id)
        if hand_run not in comparison['run_ids'] or camera_run not in comparison['run_ids']:
            raise ValueError('Select two completed runs from the same comparison')
        if not all(self.run(run)['result'] for run in (hand_run, camera_run)):
            raise ValueError('Both component results must be complete')
        identity = {'sample_id': comparison['sample_id'], 'hands': hand_run, 'camera': camera_run, 'schema': 2}
        run_id = digest(identity)
        directory = self.root / 'runs' / run_id
        with file_lock(directory / '.execution.lock'):
            existing = None
            try: existing = self.run(run_id)
            except KeyError: pass
            if not existing or not existing['result']:
                payload = json.loads(self.row(hand_run)['payload'])
                payload.update(id=run_id, algorithm='camera_combination')
                payload['backend'] = {**payload['backend'], 'label': self.run(hand_run)['label'] + ' + ' + self.run(camera_run)['label'] + ' camera'}
                if (directory / 'result').exists():
                    os.replace(directory / 'result', directory / ('previous-result-' + uuid.uuid4().hex))
                combine_camera(self.root / 'runs' / hand_run / 'result', self.root / 'runs' / camera_run / 'result', directory / 'result',
                    metadata={'run_id': run_id, 'label': payload['backend']['label']})
                with self.db() as db:
                    db.execute('INSERT INTO runs(id,payload,status,created,updated) VALUES(?,?,?,?,?) '
                               "ON CONFLICT(id) DO UPDATE SET status='completed',error=NULL,updated=excluded.updated",
                               (run_id, json.dumps(payload), 'completed', time.time(), time.time()))
        return self.attach(comparison_id, run_id)

    def attach(self, comparison_id, run_id):
        run = self.run(run_id)
        if run.get('annotation_withdrawal'):
            raise ValueError('This reference was withdrawn: ' + run['annotation_withdrawal']['reason'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM comparisons WHERE id=?', (comparison_id,)).fetchone()
            if row is None:
                raise KeyError('Comparison not found')
            stored = json.loads(row['payload'])
            if stored['sample_id'] != run['sample_id']:
                raise ValueError('Existing results must use exactly the same source, frame selection, view and calibration')
            if run_id not in stored['run_ids']:
                if len(stored['run_ids']) >= 12:
                    raise ValueError('A comparison supports up to twelve explicit runs')
                stored['run_ids'].append(run_id)
                db.execute('UPDATE comparisons SET payload=? WHERE id=?', (json.dumps(stored), comparison_id))
        return self.comparison(comparison_id)

    def close(self):
        if self._stopping.is_set():
            return
        self._stopping.set()
        with self.db() as db:
            rows = db.execute("SELECT * FROM runs WHERE owner=? AND status NOT IN ('completed','partial','failed','cancelled','interrupted')", (self.owner,)).fetchall()
        for row in rows:
            path = self.root / 'runs' / row['id']
            path.mkdir(parents=True, exist_ok=True)
            (path / 'cancel').touch()
            if process_matches(row['worker_pid'], row['worker_token'], path / 'request.json'):
                os.kill(row['worker_pid'], signal.SIGTERM)
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=2)
