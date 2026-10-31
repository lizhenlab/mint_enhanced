"""Durable owned data/training jobs. The web process never imports a model."""
from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from mint.runtime import atomic_json, digest, file_identity
from mint.inference.cache_budget import _token, file_lock
from .registry import PROJECT

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
KINDS = {'prepare_training', 'curate_dataset', 'train', 'pipeline', 'diagnostic', 'native_import', 'produce_pseudo'}


def matches(pid, token, path):
    if not pid or not token or _token(pid) != token:
        return False
    try:
        proc = Path('/proc') / str(pid)
        return proc.stat().st_uid == os.getuid() and 'mint.comparison.workflow_worker' in (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode() and str(path) in (proc / 'cmdline').read_bytes().decode()
    except OSError:
        return False


def owned_environment(kind):
    return PROJECT / '.environments' / ('training' if kind == 'train' else 'data') / 'bin/python'


class WorkflowManager:
    def __init__(self, manager, registry, *, autostart=True):
        self.manager, self.registry = manager, registry
        self.root = manager.root / 'workflows'
        self.root.mkdir(exist_ok=True)
        self.stop = threading.Event()
        self.threads = {}
        self.lock = threading.Lock()
        with manager.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, payload TEXT, status TEXT, '
                       'created REAL, updated REAL, owner_pid INTEGER, owner_token TEXT, worker_pid INTEGER, worker_token TEXT, error TEXT)')
        self._recover()
        if autostart:
            threading.Thread(target=self._loop, name='platform-workflows', daemon=True).start()
        atexit.register(self.close)

    def get(self, key):
        with self.manager.db() as db:
            row = db.execute('SELECT * FROM workflows WHERE id=?', (str(key),)).fetchone()
        if not row:
            raise KeyError('Workflow not found')
        state = dict(row)
        state['request'] = json.loads(state.pop('payload'))
        folder = self.root / state['id']
        for filename, field in (('progress.json', 'progress'), ('output.json', 'output')):
            try:
                state[field] = json.loads((folder / filename).read_text())
            except (OSError, ValueError):
                state[field] = {}
        return state

    def _set(self, key, **values):
        allowed = {'status', 'owner_pid', 'owner_token', 'worker_pid', 'worker_token', 'error'}
        if not values.keys() <= allowed:
            raise ValueError('Invalid workflow state')
        values['updated'] = time.time()
        with self.manager.db() as db:
            db.execute('UPDATE workflows SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), key])

    def preflight(self, kind, parameters):
        if kind not in KINDS or not isinstance(parameters, dict):
            raise ValueError('Choose a known workflow and parameters object')
        allowed = {'produce_pseudo': {'run_ids', 'acceleration_limit', 'split'},
                   'native_import': {'dataset', 'root', 'sequence', 'split', 'start_frame', 'max_frames', 'training'},
                   'prepare_training': {'source', 'episodes', 'max_frames'}, 'curate_dataset': {'dataset_id'},
                   'train': {'config', 'dataset_id', 'init_from', 'max_steps', 'gpus', 'resume_checkpoint', 'allow_pseudo_labels'},
                   'pipeline': {'inputs', 'gpus', 'frame_stride'}, 'diagnostic': {'profile'}}[kind]
        if parameters.keys() - allowed:
            raise ValueError('Unknown workflow parameters')
        p = dict(parameters)
        errors, snapshots = [], {}
        python = owned_environment('train') if kind == 'native_import' else owned_environment(kind) if kind in {'train', 'pipeline', 'produce_pseudo'} else Path(sys.executable)
        snapshots['interpreter'] = file_identity(python)
        if not python.is_file():
            errors.append('独立环境未安装：' + str(python))
        if kind == 'produce_pseudo':
            from .production import production_plan
            snapshots['production_plan'] = production_plan(self.manager, p.get('run_ids'))
            p.setdefault('acceleration_limit', 30.)
            import math
            if not math.isfinite(float(p['acceleration_limit'])) or not 0 < float(p['acceleration_limit']) <= 100000:
                raise ValueError('Choose a finite positive motion acceleration limit')
            p['acceleration_limit'] = float(p['acceleration_limit'])
            p.setdefault('split', 'train')
            if p['split'] not in {'train', 'validation', 'test'}:
                raise ValueError('Declare the produced partition')
            for record in snapshots['production_plan']:
                if record.get('official_split') and p['split'] != {'val': 'validation'}.get(record['official_split'], record['official_split']):
                    raise ValueError('Pseudo-label partition conflicts with the official native source split')
        if kind == 'native_import':
            from .native_sources import source_plan
            plan = source_plan(**p)
            p = {k: plan[k] for k in ('dataset', 'root', 'sequence', 'split', 'start_frame', 'max_frames', 'training')}
            snapshots['native_plan'] = plan
        if kind == 'prepare_training':
            from .datasets import dataset_info
            root, info, cameras = dataset_info(p.get('source', ''))
            p['source'] = str(root)
            snapshots['source'] = file_identity(root / 'meta/info.json')
            from .datasets import select_episode, annotation_identity
            if p.get('episodes') is None:
                p['episodes'] = [0]
            if not isinstance(p['episodes'], list) or not p['episodes'] or len(p['episodes']) > 1000:
                raise ValueError('Choose explicit episode indices')
            snapshots['episodes'] = []
            for number in p['episodes']:
                r, _, key, episode, cap = select_episode({'root': str(root), 'episode': number})
                if not cap['camera']:
                    errors.append(f'Episode {number} 缺少明确相机轨迹标注')
                snapshots['episodes'].append(annotation_identity(r, episode, key))
            if p.get('max_frames') is not None and (isinstance(p['max_frames'], bool) or int(p['max_frames']) < 1):
                raise ValueError('max_frames must be positive or omitted for the full episode')
        if kind == 'curate_dataset':
            dataset = self.registry.get(p.get('dataset_id'), 'dataset')
            if dataset.get('status') != 'reviewed_manifest':
                errors.append('请选择已接受复核的数据筛选版本')
            for entry in dataset.get('entries', []):
                split = entry.get('official_split')
                if split is None and entry.get('run_id'):
                    from .native_sources import source_provenance
                    request = json.loads(self.manager.row(entry['run_id'])['payload'])
                    split = source_provenance(self.manager, request['sample_request']['source'])['official_split']
                if split not in {None, 'train'}:
                    errors.append('官方验证 / 测试来源只能复核和评测，不能转入训练版本')
                    break
        if kind == 'train':
            import yaml
            if p.get('resume_checkpoint') and p.get('init_from'):
                raise ValueError('Choose resume_checkpoint or init_from, not both')
            path = Path(p.get('config', PROJECT / 'configs/training/mint_step2.yaml')).expanduser().resolve()
            if not path.is_file():
                raise ValueError('Training configuration is missing')
            cfg = yaml.safe_load(path.read_text())
            if 'allow_pseudo_labels' in p:
                if not isinstance(p['allow_pseudo_labels'], bool):
                    raise ValueError('allow_pseudo_labels must be a boolean opt-in')
                cfg.setdefault('data', {})['allow_pseudo_labels'] = p['allow_pseudo_labels']
            p['config'] = str(path)
            snapshots['config'] = file_identity(path)
            if p.get('dataset_id'):
                dataset = self.registry.get(p['dataset_id'], 'dataset')
                if not dataset.get('training_ready'):
                    errors.append('数据版本尚未完成格式转换和验证')
                else:
                    cfg['data']['root'] = dataset['root']
                    snapshots['dataset'] = dataset
            from mint.readiness import training_inputs
            # Check the final effective roots/initialization, not only the template.
            roots = cfg.get('data', {}).get('root', [])
            roots = roots if isinstance(roots, list) else [roots]
            snapshots['data'] = []
            for entry in roots:
                root = entry.get('root') if isinstance(entry, dict) else entry
                root = Path(root).expanduser()
                root = root if root.is_absolute() else PROJECT / root
                record = file_identity(root / 'meta/info.json')
                snapshots['data'].append(record)
                if record.get('missing'):
                    errors.append('训练数据缺失：' + str(root))
                elif (cfg.get('data', {}).get('camera_translation_normalization') or {}).get('enabled', False):
                    from .training_data import camera_normalization_schema_errors
                    errors.extend(camera_normalization_schema_errors(root))
                if cfg.get('data', {}).get('require_mano_gt', False) and (root / 'meta/info.json').is_file():
                    info = json.loads((root / 'meta/info.json').read_text())
                    if info.get('hand_supervision') != 'mano' and not (info.get('hand_supervision') == 'pseudo_mano' and cfg['data'].get('allow_pseudo_labels', False)):
                        errors.append('MANO 训练必须使用实际 MANO 参数标注的数据版本：' + str(root))
                if (root / 'meta/info.json').is_file():
                    info = json.loads((root / 'meta/info.json').read_text())
                    if info.get('hand_supervision') == 'pseudo_mano' and not cfg.get('data', {}).get('allow_pseudo_labels', False):
                        errors.append('模型伪标签需要显式允许 allow_pseudo_labels；不能当作实际 GT：' + str(root))
                provenance_path = root / 'source_manifest.json'
                if provenance_path.is_file():
                    provenance = json.loads(provenance_path.read_text())
                    records = [provenance.get('data'), provenance.get('episodes'), *provenance.get('videos', [])]
                    if any(v and file_identity(v['path']) != v for v in records):
                        errors.append('训练数据版本中的标签或视频已经变化：' + str(root))
                    snapshots.setdefault('data_provenance', []).append(file_identity(provenance_path))
            initial = None if p.get('resume_checkpoint') else p.get('init_from') or cfg.get('train', {}).get('init_from')
            if not p.get('resume_checkpoint') and not initial and cfg.get('model', {}).get('pretrained'):
                pretrained = Path(cfg['model']['pretrained']).expanduser()
                pretrained = pretrained if pretrained.is_absolute() else PROJECT / pretrained
                snapshots['pretrained_backbone'] = file_identity(pretrained)
                if not pretrained.is_file():
                    errors.append('预训练骨干权重缺失：' + str(pretrained))
            if initial:
                weight = Path(initial).expanduser()
                weight = weight if weight.is_absolute() else PROJECT / weight
                if weight.is_dir():
                    weight /= 'model.safetensors'
                p['init_from'] = str(weight)
                snapshots['initialization'] = file_identity(weight)
                if not weight.is_file():
                    errors.append('训练初始化权重缺失：' + str(weight))
            steps = p.get('max_steps')
            if steps is not None and (isinstance(steps, bool) or int(steps) != steps or int(steps) < 1):
                raise ValueError('max_steps must be a positive integer')
            if p.get('resume_checkpoint'):
                resume = Path(p['resume_checkpoint']).expanduser().resolve()
                if (not resume.is_dir() or not (resume / 'model.safetensors').is_file() or not list(resume.glob('optimizer*')) or
                    not list(resume.glob('scheduler*')) or not list(resume.glob('random_states*.pkl'))):
                    errors.append('续训检查点目录不完整')
                marker = resume / 'checkpoint.json'
                if marker.is_file():
                    saved = json.loads(marker.read_text())
                    if any(file_identity(resume / name) != identity for name, identity in saved['files'].items()):
                        errors.append('续训检查点文件已变化')
                    if len(list(resume.glob('random_states*.pkl'))) != int(p.get('gpus', 1)):
                        errors.append('严格续训需要相同 GPU/world size；改变规模请用权重初始化新实验')
                snapshots['resume_checkpoint'] = {v.name: file_identity(v) for v in resume.glob('*') if v.is_file()}
                p['resume_checkpoint'] = str(resume)
        if kind in {'train', 'pipeline', 'produce_pseudo', 'native_import'}:
            gpus = p.get('gpus', 1)
            if isinstance(gpus, bool) or int(gpus) != gpus or not 1 <= int(gpus) <= 8:
                raise ValueError('Choose 1..8 GPUs')
            if kind in {'train', 'pipeline'}:
                p['gpus'] = int(gpus)
            if python.is_file():
                modules = ['h5py', 'pyarrow', 'decord', 'cv2', 'smplx'] if kind == 'native_import' else ['ray', 'pyarrow', 'decord', 'scipy'] if kind == 'produce_pseudo' else ['accelerate', 'torch', 'safetensors', 'pyarrow', 'decord'] if kind == 'train' else ['ray', 'joblib', 'ultralytics', 'geocalib', 'moge', 'utils3d', 'droid_backends', 'lietorch']
                probe = ('import importlib.util,importlib.metadata,json,sys\n'
                         'versions={}\n'
                         'for name in sys.argv[1:]:\n'
                         ' try: versions[name]=importlib.metadata.version(name)\n'
                         ' except importlib.metadata.PackageNotFoundError: versions[name]=None\n'
                         'print(json.dumps({"missing":[n for n in sys.argv[1:] if importlib.util.find_spec(n) is None],"versions":versions}))')
                command = [str(python), '-c', probe, *modules]
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                details = json.loads(result.stdout) if result.returncode == 0 else {'missing': ['环境探测失败：' + result.stderr[-500:]], 'versions': {}}
                missing = details['missing']
                snapshots['package_versions'] = details['versions']
                if missing:
                    errors.append('独立环境缺少：' + ', '.join(missing))
        if kind == 'pipeline':
            inputs = p.get('inputs')
            if not isinstance(inputs, list) or not inputs or len(inputs) > 1000 or any(not Path(v).expanduser().exists() for v in inputs):
                raise ValueError('Choose explicit existing pipeline input paths')
            p['inputs'] = [str(Path(v).expanduser().resolve()) for v in inputs]
            stride = p.get('frame_stride', 1)
            if isinstance(stride, bool) or int(stride) != stride or int(stride) < 1:
                raise ValueError('frame_stride must be positive')
            p['frame_stride'] = int(stride)
            for asset in ('model/geocalib/pinhole.tar', 'model/moge2/model.pt', 'model/megasam/megasam_final.pth',
                          'third_party/HaWoR/scripts/scripts_test_video/hawor_video.py'):
                record = file_identity(PROJECT / asset)
                snapshots[asset] = record
                if record.get('missing'):
                    errors.append('研究数据生产资产未就绪：' + asset)
        if kind == 'diagnostic' and p.get('profile', 'train') not in {'train', 'data', 'inference', 'full'}:
            raise ValueError('Unknown diagnostic profile')
        return {'ready': not errors, 'errors': errors, 'kind': kind, 'parameters': p, 'snapshots': snapshots,
                'python': str(python), 'resource': 'Idle GPU leases; fixed world size within one training attempt' if kind == 'train' else 'Owned CPU job / pipeline GPU leases'}

    def create(self, kind, parameters, *, name=None):
        check = self.preflight(kind, parameters)
        if not check['ready']:
            raise ValueError('；'.join(check['errors']))
        implementation = [PROJECT / 'mint/comparison/workflow_worker.py', PROJECT / 'mint/comparison/training_data.py']
        if kind == 'native_import':
            implementation += [PROJECT / 'mint/comparison/native_sources.py', PROJECT / 'mint/comparison/calibration.py']
        if kind == 'produce_pseudo':
            implementation += [PROJECT / ('mint/comparison/' + name + '.py') for name in ('production', 'native_sources', 'calibration', 'quality')]
        if kind == 'train':
            implementation += sorted((PROJECT / 'model_train').rglob('*.py'))
            implementation += [PROJECT / 'mint/comparison/training_curves.py']
        request = {**check, 'name': str(name or kind)[:200], 'code': {str(p.relative_to(PROJECT)): hashlib_file(p) for p in implementation}}
        # Explicit submissions can be repeated; restart/retry keeps this attempt chain.
        key = digest({'request': request, 'submitted': time.time_ns()})
        request['id'] = key
        folder = self.root / key
        folder.mkdir()
        atomic_json(folder / 'request.json', request)
        with self.manager.db() as db:
            db.execute('INSERT INTO workflows(id,payload,status,created,updated) VALUES(?,?,?,?,?)',
                       (key, json.dumps(request), 'queued', time.time(), time.time()))
        return self.get(key)

    def cancel(self, key):
        state = self.get(key)
        if state['status'] not in TERMINAL:
            (self.root / key / 'cancel').touch()
            self._set(key, status='cancelled' if state['status'] == 'queued' else 'cancelling')
        return self.get(key)

    def resume(self, key):
        state = self.get(key)
        if state['status'] in {'failed', 'cancelled', 'interrupted'}:
            check = self.preflight(state['request']['kind'], state['request']['parameters'])
            if not check['ready'] or check['snapshots'] != state['request']['snapshots']:
                raise ValueError('Workflow inputs/environment changed; create a new explicit task')
            (self.root / key / 'cancel').unlink(missing_ok=True)
            (self.root / key / 'exit.json').unlink(missing_ok=True)
            self._set(key, status='queued', error=None)
        return self.get(key)

    def _recover(self):
        with self.manager.db() as db:
            rows = db.execute("SELECT * FROM workflows WHERE status NOT IN ('completed','failed','cancelled','interrupted','queued')").fetchall()
        for row in rows:
            path = self.root / row['id'] / 'request.json'
            if row['owner_pid'] and _token(row['owner_pid']) == row['owner_token']:
                continue
            if matches(row['worker_pid'], row['worker_token'], path):
                with self.lock:
                    thread = threading.Thread(target=self._watch, args=(row['id'], row['worker_pid'], row['worker_token']), daemon=True)
                    self.threads[row['id']] = thread
                    self._set(row['id'], owner_pid=os.getpid(), owner_token=_token(os.getpid()))
                    thread.start()
            else:
                self._finish(row['id'])

    def _finish(self, key):
        try:
            exit_state = json.loads((self.root / key / 'exit.json').read_text())
        except (OSError, ValueError):
            exit_state = {'status': 'interrupted', 'error': 'Worker exited without a completion record; explicit resume reuses a complete training checkpoint'}
        self._set(key, status=exit_state['status'], error=exit_state.get('error'))

    def _watch(self, key, pid, token):
        path = self.root / key / 'request.json'
        while not self.stop.wait(.5) and matches(pid, token, path):
            if self.get(key)['status'] == 'cancelling':
                (path.parent / 'cancel').touch()
        if not self.stop.is_set():
            self._finish(key)

    def _loop(self):
        while not self.stop.wait(.5) and not self.manager._stopping.is_set():
            with self.lock:
                self.threads = {k: v for k, v in self.threads.items() if v.is_alive()}
                if self.threads:
                    continue
            with self.manager.db() as db:
                row = db.execute("SELECT id FROM workflows WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if not row:
                    continue
                changed = db.execute("UPDATE workflows SET status='launching',owner_pid=?,owner_token=?,updated=? WHERE id=? AND status='queued'",
                    (os.getpid(), _token(os.getpid()), time.time(), row['id'])).rowcount
            if changed:
                thread = threading.Thread(target=self._execute, args=(row['id'],), daemon=True)
                with self.lock:
                    self.threads[row['id']] = thread
                thread.start()

    def _execute(self, key):
        path = self.root / key / 'request.json'
        try:
            with file_lock(path.parent / '.execution.lock', blocking=False):
                state = self.get(key)
                request = state['request']
                if json.loads(path.read_text()) != request:
                    raise ValueError('Workflow request file changed after submission')
                if any(hashlib_file(PROJECT / name) != sha for name, sha in request['code'].items()):
                    raise ValueError('Workflow implementation changed; create a new task under this release')
                check = self.preflight(request['kind'], request['parameters'])
                if not check['ready'] or check['snapshots'] != request['snapshots']:
                    raise ValueError('Frozen workflow inputs changed or are unavailable')
                env = os.environ.copy()
                env.update(PYTHONPATH=str(PROJECT), PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
                    CUDA_VISIBLE_DEVICES='', MINT_MANO_DEVICE='cpu', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2',
                    MINT_COMPARISON_DIR=str(self.manager.root), MINT_TASK_PRIORITY='background',
                    MINT_WORKFLOW_PARENT_PID=str(os.getpid()), MINT_WORKFLOW_PARENT_TOKEN=str(_token(os.getpid())))
                with (path.parent / 'worker.log').open('ab', buffering=0) as log:
                    worker_python = request['python'] if request['kind'] in {'native_import', 'produce_pseudo'} else sys.executable
                    child = subprocess.Popen([worker_python, '-m', 'mint.comparison.workflow_worker', '--request', str(path)],
                        cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
                token = _token(child.pid)
                self._set(key, status='running', worker_pid=child.pid, worker_token=token)
                cancelled_at = None
                while child.poll() is None:
                    if self.stop.is_set() or self.manager._stopping.is_set() or (path.parent / 'cancel').exists():
                        (path.parent / 'cancel').touch()
                        cancelled_at = cancelled_at or time.monotonic()
                        if time.monotonic() - cancelled_at > 5 and matches(child.pid, token, path):
                            os.killpg(child.pid, signal.SIGTERM)
                        if time.monotonic() - cancelled_at > 15 and matches(child.pid, token, path):
                            os.killpg(child.pid, signal.SIGKILL)
                    time.sleep(.3)
                self._finish(key)
                if self.stop.is_set():
                    self._set(key, status='interrupted', error='Service stopped; resume training from its last complete checkpoint')
        except BaseException as error:
            self._set(key, status='failed', error=str(error))

    def task_rows(self):
        with self.manager.db() as db:
            ids = [row['id'] for row in db.execute('SELECT id FROM workflows ORDER BY created DESC')]
        rows = []
        for key in ids:
            state = self.get(key)
            rows.append({'id': key, 'kind': 'workflow', 'label': state['request']['name'], 'source': state['request']['kind'],
                'status': state['status'], 'updated': state['updated'], 'owner_pid': state.get('owner_pid'),
                'worker_pid': state.get('worker_pid'), 'progress': state['progress'], 'error': state.get('error'),
                'url': '/compare?workflow=' + key, 'recovery_policy': 'explicit_checkpoint_resume'})
        return rows

    def close(self):
        self.stop.set()


def hashlib_file(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
