"""Owned background MuJoCo/robot rendering, independently cancellable from inference."""
from __future__ import annotations

import atexit
import importlib.util
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from mint.runtime import atomic_json, digest, file_identity
from mint.inference.cache_budget import file_lock, _token
from mint.inference.gpu_scheduler import GpuLease, NvidiaSmiProbe
from .contracts import load_result
from .registry import PROJECT


def capabilities():
    mano = all((PROJECT / 'assets/mano' / folder / file).is_file() for folder, file in
               [('mano_left', 'MANO_LEFT.pkl'), ('mano_right', 'MANO_RIGHT.pkl')])
    robot = PROJECT / 'eval/simulate/wuji-retargeting'
    mujoco = importlib.util.find_spec('mujoco') is not None
    robot_ready = (robot / 'example/config/adaptive_analytical_wuji_glove_left.yaml').is_file() and (robot / 'wuji_retargeting/wuji-description').is_dir()
    return {'mujoco': {'ready': mujoco and mano, 'reason': '' if mujoco and mano else '需要 MuJoCo 与已授权 MANO 资产'},
            'retarget': {'ready': mujoco and robot_ready, 'reason': '' if mujoco and robot_ready else '需要 MuJoCo、Wuji Hand 模型和 IK 配置'},
            'software': os.environ.get('MUJOCO_GL') == 'osmesa',
            'scope': 'Visualization/kinematic retargeting; does not change prediction geometry or prove contact/dynamics accuracy'}


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
        if state['status'] == 'running' and not process_matches(state.get('pid'), state.get('pid_token'), path / 'request.json'):
            state.update(status='interrupted', error='Rendering process is no longer running')
        if state['status'] == 'completed' and not (path / 'video.mp4').is_file():
            state.update(status='evicted', error='Render cache was released; generate it again')
        return {**request, **state, 'url': f'/api/comparison/tools/{job_id}/video' if state['status'] == 'completed' else None}

    def history(self, limit=100):
        jobs = []
        for path in sorted(self.root.glob('*/request.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                jobs.append(self.status(path.parent.name))
            except (OSError, ValueError):
                continue
        return jobs

    def start(self, run_id, *, kind='mujoco', space='camera', start=0, end=None, width=640, include_absent=False, resource_policy='shared'):
        if kind not in {'mujoco', 'retarget'} or space not in {'camera', 'world'}:
            raise ValueError('Choose a known rendering tool and coordinate frame')
        capability = capabilities()[kind]
        if not capability['ready']:
            raise ValueError(capability['reason'])
        if resource_policy not in {'shared', 'idle'}:
            raise ValueError('Rendering uses shared or idle GPU policy')
        run = self.manager.run(run_id)
        metadata, arrays = load_result(self.manager.root / 'runs' / run_id / 'result')
        with self.manager.db() as db:
            payload = json.loads(db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()['payload'])
        sample = json.loads(Path(payload['sample_manifest']).read_text())
        end = sample['frames'] if end in (None, '') else end
        if any(isinstance(value, bool) or float(value) != int(value) for value in (start, end, width)):
            raise ValueError('Frame bounds and width must be integers')
        if not isinstance(include_absent, bool):
            raise ValueError('include_absent must be a boolean')
        start, end, width = int(start), int(end), int(width)
        if not 0 <= start < end <= sample['frames'] or not 320 <= width <= 1280 or width % 2:
            raise ValueError('Choose valid sample frame bounds and an even width from 320–1280')
        if kind == 'mujoco' and 'vertices_camera_m' not in arrays:
            raise ValueError('MuJoCo mesh rendering requires MANO vertices in this result')
        if space == 'world' and 'T_world_camera' not in arrays:
            raise ValueError('世界渲染需要全部结果帧的有效世界相机；可使用相机系查看此结果')
        identity = {'run_id': run_id, 'result': file_identity(self.manager.root / 'runs' / run_id / 'result/result.json'),
            'kind': kind, 'space': space, 'start': start, 'end': end, 'width': width,
            'include_absent': bool(include_absent), 'resource_policy': resource_policy,
            'implementation': digest({str(p.relative_to(PROJECT)): file_identity(p) for p in
                [PROJECT / 'mint/comparison/tool_worker.py', *sorted((PROJECT / 'eval/model_effect/visualization/render').glob('*.py'))]})}
        job_id = digest(identity)
        folder = self.root / job_id
        folder.mkdir(parents=True, exist_ok=True)
        with file_lock(folder / '.submit.lock'):
            if (folder / 'request.json').is_file():
                old = self.status(job_id)
                if old['status'] in {'queued', 'waiting_gpu', 'running', 'completed'}:
                    return old
            request = {**identity, 'id': job_id, 'label': run['label'], 'sample_manifest': payload['sample_manifest'],
                       'result_dir': str(self.manager.root / 'runs' / run_id / 'result'), 'created': time.time()}
            atomic_json(folder / 'request.json', request)
            (folder / 'cancel').unlink(missing_ok=True)
            atomic_json(folder / 'status.json', {'id': job_id, 'status': 'queued', 'updated': time.time()})
        return self.status(job_id)

    def cancel(self, job_id):
        state = self.status(job_id)
        if state['status'] in {'queued', 'waiting_gpu', 'running'}:
            (self.root / job_id / 'cancel').touch()
            if state['status'] != 'running':
                atomic_json(self.root / job_id / 'status.json', {'id': job_id, 'status': 'cancelled', 'updated': time.time()})
        return self.status(job_id)

    def _lease(self, request, folder):
        while not self.stop.is_set() and not (folder / 'cancel').exists():
            try:
                samples = self.probe()
                for uuid, gpu in sorted(samples.items(), key=lambda pair: -pair[1].free_mib):
                    if gpu.free_mib < 2048 or gpu.utilization > (35 if request['resource_policy'] == 'shared' else 10):
                        continue
                    if request['resource_policy'] == 'idle' and gpu.foreign_pids:
                        continue
                    lease = GpuLease(uuid)
                    if lease.acquire():
                        return uuid, lease, bool(gpu.foreign_pids)
            except Exception as error:
                atomic_json(folder / 'status.json', {'id': request['id'], 'status': 'waiting_gpu', 'error': str(error), 'updated': time.time()})
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
                    atomic_json(folder / 'status.json', {'id': job['id'], 'status': 'failed', 'error': str(error), 'updated': time.time()})

    def _execute(self, job, folder):
        child, lease, token = None, None, None
        try:
            env = os.environ.copy()
            env.update(PYTHONPATH=str(PROJECT), PYTHONDONTWRITEBYTECODE='1', MINT_MANO_DEVICE='cpu')
            if env.get('MUJOCO_GL') != 'osmesa':
                uuid, lease, foreign = self._lease(job, folder)
                # MuJoCo EGL enumerates physical devices, independently of CUDA_VISIBLE_DEVICES.
                listing = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid', '--format=csv,noheader,nounits'], text=True)
                index = next(line.split(',')[0].strip() for line in listing.splitlines() if uuid in line)
                env.update(MUJOCO_GL='egl', MUJOCO_EGL_DEVICE_ID=index, CUDA_VISIBLE_DEVICES=uuid,
                           MINT_RENDER_SHARED_FOREIGN='1' if foreign and job['resource_policy'] == 'shared' else '0')
            if self.stop.is_set() or (folder / 'cancel').exists():
                raise InterruptedError('Rendering cancelled before launch')
            python = Path(__import__('sys').executable)
            with (folder / 'worker.log').open('ab', buffering=0) as log:
                child = subprocess.Popen([str(python), '-m', 'mint.comparison.tool_worker', '--request', str(folder / 'request.json')],
                    cwd=PROJECT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                    pass_fds=(lease.file.fileno(),) if lease else ())
            token = _token(child.pid)
            atomic_json(folder / 'status.json', {'id': job['id'], 'status': 'running', 'pid': child.pid, 'pid_token': token, 'updated': time.time()})
            cancelled_at = None
            while child.poll() is None:
                if self.stop.is_set() or (folder / 'cancel').exists():
                    (folder / 'cancel').touch()
                    cancelled_at = cancelled_at or time.monotonic()
                    if time.monotonic() - cancelled_at > 5 and process_matches(child.pid, token, folder / 'request.json'):
                        os.kill(child.pid, signal.SIGTERM)
                    if time.monotonic() - cancelled_at > 15:
                        break
                time.sleep(.25)
            if json.loads((folder / 'status.json').read_text())['status'] == 'running':
                atomic_json(folder / 'status.json', {'id': job['id'], 'status': 'cancelled' if cancelled_at else 'failed', 'error': 'Renderer exited without a completion marker', 'updated': time.time()})
        finally:
            if child is not None:
                from mint.service import _reap_session
                _reap_session(child, token or '0', grace=3)
            if lease:
                lease.release()

    def close(self):
        self.stop.set()
