"""Owned background MuJoCo/robot rendering, independently cancellable from inference."""
from __future__ import annotations

import atexit
import importlib.util
import json
import os
import signal
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, digest, file_identity
from mint.inference.cache_budget import file_lock, _token
from mint.inference.gpu_scheduler import GpuLease, NvidiaSmiProbe
from .contracts import integer_number, load_result
from .registry import PROJECT


def capabilities():
    mano = all((PROJECT / 'assets/mano' / folder / file).is_file() for folder, file in
               [('mano_left', 'MANO_LEFT.pkl'), ('mano_right', 'MANO_RIGHT.pkl')])
    robot = PROJECT / 'eval/simulate/wuji-retargeting'
    def missing_modules(names):
        return [name for name in names if importlib.util.find_spec(name) is None]
    common = missing_modules(('mujoco', 'cv2', 'scipy'))
    common += [name for name in ('ffmpeg', 'ffprobe') if not shutil.which(name)]
    mesh_missing = common + missing_modules(('torch', 'smplx')) + ([] if mano else ['已授权 MANO 左右手资产'])
    body = robot / 'wuji_retargeting/wuji-description/hand/body'
    robot_files = [robot / f'example/config/adaptive_analytical_wuji_glove_{side}.yaml' for side in ('left', 'right')]
    robot_files += [body / kind / f'{side}.{extension}' for side in ('left', 'right')
                    for kind, extension in (('urdf', 'urdf'), ('mjcf', 'xml'))]
    robot_missing = common + missing_modules(('pinocchio', 'nlopt', 'yaml'))
    robot_ready = all(path.is_file() for path in robot_files) and (robot / 'wuji_retargeting/__init__.py').is_file()
    if robot_ready:
        try:
            for side in ('left', 'right'):
                path = body / 'mjcf' / f'{side}.xml'
                tree = ET.parse(path).getroot(); compiler = tree.find('compiler')
                meshdir = compiler.get('meshdir', '') if compiler is not None else ''
                meshes = tree.findall('./asset/mesh')
                robot_ready &= bool(meshes) and all((path.parent / meshdir / mesh.get('file', '')).is_file() for mesh in meshes)
        except (OSError, ET.ParseError):
            robot_ready = False
    if not robot_ready:
        robot_missing.append('Wuji Hand 左右手模型、IK 配置与运行代码')
    return {'mujoco': {'ready': not mesh_missing, 'reason': '需要：' + '、'.join(mesh_missing) if mesh_missing else '', 'missing': mesh_missing},
            'retarget': {'ready': not robot_missing, 'reason': '需要：' + '、'.join(robot_missing) if robot_missing else '', 'missing': robot_missing},
            'software': os.environ.get('MUJOCO_GL') == 'osmesa',
            'verification_level': 'dependencies_and_required_assets_presence',
            'scope': 'Visualization/kinematic retargeting; does not change prediction geometry or prove contact/dynamics accuracy'}


def frame_mapping(metadata, arrays, sample, *, sample_id=None):
    """Map source-frame IDs to sample positions without inventing absent rows."""
    n = integer_number(sample.get('frames'), 'Sample frames', minimum=1)
    ids = np.asarray(sample.get('source_frame_indices'))
    if ids.shape != (n,) or ids.dtype.kind not in 'iu' or np.any(ids < 0) or np.any(ids[1:] <= ids[:-1]):
        raise ValueError('Rendering requires an increasing input sample timeline')
    expected = sample_id or metadata.get('sample_id')
    if metadata.get('sample_id') != expected or sample.get('id', expected) != expected:
        raise ValueError('Rendering result and input sample do not match')
    source = np.asarray(arrays['source_frame_indices'])
    positions = np.searchsorted(source, ids)
    matched = positions < len(source)
    matched[matched] &= source[positions[matched]] == ids[matched]
    source_positions = np.searchsorted(ids, source)
    source_matched = source_positions < n
    source_matched[source_matched] &= ids[source_positions[source_matched]] == source[source_matched]
    if not source_matched.all():
        raise ValueError('Rendering result contains frames outside the input sample timeline')
    # Old internal fixtures have IDs only. Real sample manifests also declare
    # timestamps; matching IDs must never hide different temporal alignment.
    if 'timestamps_s' in sample:
        times = np.asarray(sample['timestamps_s'], float)
        if times.shape != (n,) or not np.isfinite(times).all() or times[0] < 0 or np.any(np.diff(times) <= 0):
            raise ValueError('Rendering requires valid sample timestamps')
        if not np.allclose(times[matched], arrays['timestamps_s'][positions[matched]], atol=1e-5, rtol=1e-6):
            raise ValueError('Rendering result timestamps do not match the input sample timeline')
    for key in ('width', 'height'):
        if key in sample:
            integer_number(sample[key], 'Sample ' + key, minimum=1)
    if 'fps' in sample and (isinstance(sample['fps'], bool) or not np.isfinite(float(sample['fps'])) or float(sample['fps']) <= 0):
        raise ValueError('Rendering requires a positive sample FPS')
    return ids, positions, matched


def contiguous_ranges(mask):
    edges = np.diff(np.r_[False, np.asarray(mask, bool), False].astype(np.int8))
    return [[int(start), int(end)] for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]


def write_status(folder, values):
    """Worker and controller share one state lock, including fast completion."""
    with file_lock(Path(folder) / '.state.lock'):
        if values.get('status') in {'queued', 'waiting_gpu', 'running', 'completed'} and (Path(folder) / 'cancel').exists():
            raise InterruptedError('Rendering cancelled before publication')
        atomic_json(Path(folder) / 'status.json', values)


def render_implementation(kind):
    files = [PROJECT / 'mint/comparison/tool_worker.py', PROJECT / 'mint/visualization/encoding.py',
             *sorted((PROJECT / 'eval/model_effect/visualization/render').glob('*.py'))]
    if kind == 'mujoco':
        files += [PROJECT / f'assets/mano/mano_{side}/MANO_{side.upper()}.pkl' for side in ('left', 'right')]
    else:
        robot = PROJECT / 'eval/simulate/wuji-retargeting'
        files += sorted(path for path in robot.rglob('*') if path.is_file() and path.suffix.lower() in {'.py', '.yaml', '.urdf', '.xml', '.stl', '.obj'})
    return digest({str(path.relative_to(PROJECT)): file_identity(path) for path in files})


def process_matches(pid, token, path):
    if not pid or _token(pid) != token:
        return False
    try:
        proc = Path('/proc') / str(pid)
        cmd = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode()
        return proc.stat().st_uid == os.getuid() and 'mint.comparison.tool_worker' in cmd and str(path) in cmd
    except OSError:
        return False


class RenderTools:
    def __init__(self, manager, *, autostart=True):
        self.manager = manager
        self.root = manager.root / 'tools'
        self.root.mkdir(parents=True, exist_ok=True)
        self.stop = threading.Event()
        self.probe = NvidiaSmiProbe()
        if autostart:
            threading.Thread(target=self._loop, name='comparison-render-tools', daemon=True).start()
        atexit.register(self.close)

    def status(self, job_id):
        if len(str(job_id)) != 64 or any(c not in '0123456789abcdef' for c in str(job_id)):
            raise KeyError('Invalid render job ID')
        path = self.root / job_id
        if not (path / 'request.json').is_file():
            raise KeyError('Render job not found')
        request = json.loads((path / 'request.json').read_text())
        state = json.loads((path / 'status.json').read_text())
        if request.get('run_id') and state['status'] in {'queued', 'waiting_gpu', 'running', 'completed'}:
            try:
                self._assert_source(request)
            except (OSError, ValueError, KeyError) as error:
                return {**request, **state, 'status': 'failed', 'source_invalidated': True, 'url': None,
                        'error': 'Render source is unavailable or withdrawn: ' + str(error)}
        if state['status'] == 'running' and not process_matches(state.get('pid'), state.get('pid_token'), path / 'request.json'):
            state.update(status='interrupted', error='Rendering process is no longer running')
        if state['status'] == 'completed':
            outputs = {'video': path / 'video.mp4', 'mapping': path / 'mapping.json'}
            if any(not output.is_file() or not output.stat().st_size for output in outputs.values()):
                state.update(status='evicted', error='Render video or frame mapping was released; generate it again')
            elif state.get('outputs') and any(file_identity(output) != state['outputs'].get(key) for key, output in outputs.items()):
                state.update(status='evicted', error='Rendered output files changed; generate them again')
            else:
                try:
                    mapping = json.loads(outputs['mapping'].read_text())
                    if not isinstance(mapping.get('source_frame_indices'), list) or ('start' in request and
                            len(mapping['source_frame_indices']) != request['end'] - request['start']):
                        raise ValueError('Frame mapping length does not match the render interval')
                except (OSError, ValueError, KeyError, TypeError) as error:
                    state.update(status='evicted', error='Rendered frame mapping is unavailable or invalid: ' + str(error))
                else:
                    state['output_integrity'] = 'identity_verified' if state.get('outputs') else 'legacy_presence_and_mapping'
        return {**request, **state, 'url': f'/api/comparison/tools/{job_id}/video' if state['status'] == 'completed' else None}

    def _assert_source(self, request):
        source = self.manager.run(request['run_id'], summary=True)
        if not source['result']:
            raise ValueError(source.get('error') or 'Render source is unavailable or withdrawn')

    def history(self, limit=100):
        jobs = []
        for path in sorted(self.root.glob('*/request.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                jobs.append(self.status(path.parent.name))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return jobs

    def _inputs(self, run_id):
        run = self.manager.run(run_id, summary=True)
        if not run.get('result'):
            raise ValueError('Rendering requires an existing published geometry result')
        directory = self.manager.root / 'runs' / run_id / 'result'
        result_identity = file_identity(directory / 'result.json')
        metadata, arrays = load_result(directory)
        with self.manager.db() as db:
            row = db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise KeyError('Render source run not found')
        payload = json.loads(row['payload'])
        manifest = Path(payload['sample_manifest'])
        identity = file_identity(manifest)
        sample = json.loads(manifest.read_text())
        ids, positions, matched = frame_mapping(metadata, arrays, sample, sample_id=run['sample_id'])
        if file_identity(manifest) != identity:
            raise ValueError('Input sample changed while reading render settings')
        if file_identity(directory / 'result.json') != result_identity:
            raise ValueError('Source result changed while reading render settings')
        return run, directory, result_identity, metadata, arrays, manifest, identity, sample, ids, positions, matched

    def options(self, run_id):
        run, directory, result_identity, metadata, arrays, manifest, identity, sample, ids, positions, matched = self._inputs(run_id)
        world = np.zeros(len(ids), bool)
        if 'T_world_camera' in arrays:
            world[matched] = np.asarray(arrays['pose_valid'])[positions[matched]]
        return {'run_id': run_id, 'label': run['label'], 'sample_id': run['sample_id'],
                'sample_frames': len(ids), 'result_frames': len(arrays['source_frame_indices']),
                'mesh_available': 'vertices_camera_m' in arrays, 'world_available': bool(world.any()),
                'available_ranges': contiguous_ranges(matched), 'world_ranges': contiguous_ranges(world),
                'range_convention': 'Zero-based sample positions [start,end); UI displays start+1 through end',
                'scope': 'Published result applicability only; does not start inference or rendering'}

    def start(self, run_id, *, kind='mujoco', space='camera', start=0, end=None, width=640, include_absent=False, resource_policy='shared'):
        if kind not in {'mujoco', 'retarget'} or space not in {'camera', 'world'}:
            raise ValueError('Choose a known rendering tool and coordinate frame')
        capability = capabilities()[kind]
        if not capability['ready']:
            raise ValueError(capability['reason'])
        if resource_policy not in {'shared', 'idle'}:
            raise ValueError('Rendering uses shared or idle GPU policy')
        run, directory, result_identity, metadata, arrays, manifest, sample_identity, sample, full_ids, full_positions, full_matched = self._inputs(run_id)
        end = sample['frames'] if end in (None, '') else end
        start = integer_number(start, 'Start sample frame')
        end = integer_number(end, 'End sample frame', minimum=1)
        width = integer_number(width, 'Render width', minimum=320, maximum=1280)
        if not isinstance(include_absent, bool):
            raise ValueError('include_absent must be a boolean')
        if not 0 <= start < end <= sample['frames'] or not 320 <= width <= 1280 or width % 2:
            raise ValueError('Choose valid sample frame bounds and an even width from 320–1280')
        if kind == 'mujoco' and 'vertices_camera_m' not in arrays:
            raise ValueError('MuJoCo mesh rendering requires MANO vertices in this result')
        if space == 'world' and 'T_world_camera' not in arrays:
            raise ValueError('世界渲染需要全部结果帧的有效世界相机；可使用相机系查看此结果')
        ids, positions, matched = full_ids[start:end], full_positions[start:end], full_matched[start:end]
        if not matched.all():
            raise ValueError('工具所选片段含原结果缺失帧，请选择完整覆盖的片段')
        if space == 'world' and not arrays['pose_valid'][positions].all():
            raise ValueError('World rendering requires valid camera geometry for each selected frame')
        visible = np.asarray(arrays['geometry_valid'][positions]).copy()
        if not include_absent and 'presence' in arrays:
            visible &= arrays['presence'][positions]
        if not visible.any():
            raise ValueError('The selected interval has no visible hand geometry')
        identity = {'schema': 2, 'run_id': run_id, 'result': result_identity,
            'sample': sample_identity, 'sample_id': run['sample_id'],
            'kind': kind, 'space': space, 'start': start, 'end': end, 'width': width,
            'include_absent': bool(include_absent), 'resource_policy': resource_policy,
            'implementation': render_implementation(kind)}
        job_id = digest(identity)
        folder = self.root / job_id
        folder.mkdir(parents=True, exist_ok=True)
        with file_lock(folder / '.submit.lock'):
            if (folder / 'request.json').is_file():
                old = self.status(job_id)
                if old['status'] in {'queued', 'waiting_gpu', 'running', 'completed'}:
                    return old
            request = {**identity, 'id': job_id, 'label': run['label'], 'sample_manifest': str(manifest),
                       'result_dir': str(self.manager.root / 'runs' / run_id / 'result'), 'created': time.time()}
            try:
                # A terminated worker can still be in controller cleanup. Do
                # not reset its state until that execution has released ownership.
                with file_lock(folder / '.execution.lock', blocking=False), file_lock(folder / '.state.lock'):
                    atomic_json(folder / 'request.json', request)
                    (folder / 'cancel').unlink(missing_ok=True)
                    atomic_json(folder / 'status.json', {'id': job_id, 'status': 'queued', 'updated': time.time()})
            except BlockingIOError:
                raise ValueError('Previous rendering is still cleaning up; retry shortly') from None
        return self.status(job_id)

    def cancel(self, job_id):
        self.status(job_id)  # Validate the ID before constructing any writable lock path.
        with file_lock(self.root / job_id / '.state.lock'):
            state = self.status(job_id)
            if state['status'] in {'queued', 'waiting_gpu', 'running'}:
                (self.root / job_id / 'cancel').touch()
                if state['status'] != 'running':
                    atomic_json(self.root / job_id / 'status.json', {'id': job_id, 'status': 'cancelled', 'updated': time.time()})
        return self.status(job_id)

    def _lease(self, request, folder):
        while not self.stop.is_set() and not (folder / 'cancel').exists():
            self._assert_source(request)
            try:
                samples = self.probe()
            except Exception as error:
                if not (folder / 'cancel').exists():
                    write_status(folder, {'id': request['id'], 'status': 'waiting_gpu', 'error': str(error), 'updated': time.time()})
                self.stop.wait(2)
                continue
            # A probe can itself take time. Keep source withdrawal outside the
            # retryable GPU-error handler so an invalid job releases the slot.
            self._assert_source(request)
            try:
                for uuid, gpu in sorted(samples.items(), key=lambda pair: -pair[1].free_mib):
                    if gpu.free_mib < 2048 or gpu.utilization > (35 if request['resource_policy'] == 'shared' else 10):
                        continue
                    if request['resource_policy'] == 'idle' and gpu.foreign_pids:
                        continue
                    lease = GpuLease(uuid)
                    if lease.acquire():
                        return uuid, lease, bool(gpu.foreign_pids)
                write_status(folder, {'id': request['id'], 'status': 'waiting_gpu',
                    'waiting_reason': '等待满足显存、负载与共享策略的 GPU；尚未开始渲染', 'updated': time.time()})
            except Exception as error:
                if not (folder / 'cancel').exists():
                    write_status(folder, {'id': request['id'], 'status': 'waiting_gpu', 'error': str(error), 'updated': time.time()})
            self.stop.wait(2)
        raise InterruptedError('Rendering cancelled while waiting for GPU headroom')

    def _loop(self):
        while not self.stop.wait(.5):
            jobs = self.history(limit=None)
            if any(job['status'] == 'running' for job in jobs):
                continue
            for job in reversed(jobs):
                if self.stop.is_set():
                    break
                if job['status'] not in {'queued', 'waiting_gpu'}:
                    continue
                folder = self.root / job['id']
                try:
                    with file_lock(self.root / '.render-slot', blocking=False), file_lock(folder / '.execution.lock', blocking=False):
                        self._execute(job, folder)
                except BlockingIOError:
                    continue
                except Exception as error:
                    with file_lock(folder / '.state.lock'):
                        current = self.status(job['id'])
                        if current['status'] not in {'completed', 'cancelled'}:
                            atomic_json(folder / 'status.json', {'id': job['id'],
                                'status': 'cancelled' if isinstance(error, InterruptedError) or (folder / 'cancel').exists() else 'failed',
                                'error': str(error), 'updated': time.time()})

    def _execute(self, job, folder):
        from mint.service import _child_exited, _reap_session
        # The scheduler snapshot may predate another owner completing/cancelling
        # this request. Recheck after the execution lock, before taking a GPU.
        current = self.status(job['id'])
        if current['status'] not in {'queued', 'waiting_gpu'}:
            return
        job = current
        child, lease, token = None, None, None
        try:
            env = os.environ.copy()
            env.update(PYTHONPATH=str(PROJECT), PYTHONDONTWRITEBYTECODE='1', MINT_MANO_DEVICE='cpu')
            if env.get('MUJOCO_GL') != 'osmesa':
                uuid, lease, foreign = self._lease(job, folder)
                self._assert_source(job)
                # MuJoCo EGL enumerates physical devices, independently of CUDA_VISIBLE_DEVICES.
                listing = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid', '--format=csv,noheader,nounits'], text=True)
                index = next(line.split(',')[0].strip() for line in listing.splitlines() if uuid in line)
                env.update(MUJOCO_GL='egl', MUJOCO_EGL_DEVICE_ID=index, CUDA_VISIBLE_DEVICES=uuid,
                           MINT_RENDER_SHARED_FOREIGN='1' if foreign and job['resource_policy'] == 'shared' else '0')
            if self.stop.is_set() or (folder / 'cancel').exists():
                raise InterruptedError('Rendering cancelled before launch')
            python = Path(__import__('sys').executable)
            # The child uses the same lock before publishing progress/completion,
            # so a fast exit cannot be overwritten by this initial running state.
            with file_lock(folder / '.state.lock'), (folder / 'worker.log').open('ab', buffering=0) as log:
                if (folder / 'cancel').exists():
                    raise InterruptedError('Rendering cancelled before launch')
                self._assert_source(job)
                child = subprocess.Popen([str(python), '-m', 'mint.comparison.tool_worker', '--request', str(folder / 'request.json')],
                    cwd=PROJECT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                    pass_fds=(lease.file.fileno(),) if lease else ())
                token = _token(child.pid)
                atomic_json(folder / 'status.json', {'id': job['id'], 'status': 'running', 'pid': child.pid, 'pid_token': token, 'updated': time.time()})
            cancelled_at = None
            while not _child_exited(child):
                self._assert_source(job)
                if self.stop.is_set() or (folder / 'cancel').exists():
                    (folder / 'cancel').touch()
                    cancelled_at = cancelled_at or time.monotonic()
                    if time.monotonic() - cancelled_at > 5 and process_matches(child.pid, token, folder / 'request.json'):
                        os.kill(child.pid, signal.SIGTERM)
                    if time.monotonic() - cancelled_at > 15:
                        break
                time.sleep(.25)
            with file_lock(folder / '.state.lock'):
                if json.loads((folder / 'status.json').read_text())['status'] == 'running':
                    atomic_json(folder / 'status.json', {'id': job['id'], 'status': 'cancelled' if cancelled_at else 'failed', 'error': 'Renderer exited without a completion marker', 'updated': time.time()})
        finally:
            try:
                if child is not None:
                    try:
                        _reap_session(child, token or '0', grace=3)
                    finally:
                        child.wait(timeout=5)
            finally:
                if lease:
                    lease.release()

    def close(self):
        self.stop.set()
