"""One owned process, one native framework environment, atomic status and result."""
from __future__ import annotations

import argparse
import json
import os
import resource
import signal
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, file_identity
from mint.inference.base import InferenceCancelled
from mint.inference.cache_budget import file_lock
from .contracts import publish_result


class WorkerContext:
    def __init__(self, request_path):
        self.path = Path(request_path)
        self.request = json.loads(self.path.read_text())
        self.directory = self.path.parent
        self.run_id = self.request['id']
        self.spec, self.options = self.request['backend'], self.request['options']
        self.sample = json.loads(Path(self.request['sample_manifest']).read_text())
        self.work = self.directory / 'native'
        self.work.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.started = time.perf_counter()
        self.timings, self.metadata = {}, {}
        self._last_progress = self._last_probe = 0
        self._last_stage, self._foreign = None, False
        self.frames = None

    def cancelled(self):
        return (self.directory / 'cancel').exists()

    def check_cancel(self):
        if self.cancelled():
            raise InferenceCancelled('Comparison run cancelled')

    def progress(self, **values):
        self.check_cancel()
        now = time.monotonic()
        stage = values.get('stage')
        if now - self._last_progress > .25 or stage != self._last_stage:
            self._last_progress, self._last_stage = now, stage
            atomic_json(self.directory / 'progress.json', {**values, 'updated_at': time.time(), 'pid': os.getpid()})

    def cooperate(self, started):
        self.check_cancel()
        if self.options['resource_policy'] != 'shared':
            return
        import torch
        torch.cuda.synchronize()
        compute_seconds=max(0.,time.perf_counter()-started)
        now = time.monotonic()
        if now - self._last_probe > 5:
            from mint.inference.gpu_scheduler import NvidiaSmiProbe
            try:
                selected = self.request.get('gpu_uuids', [])
                cached=None
                state=os.environ.get('MINT_NATIVE_GPU_STATE')
                if state:
                    try:
                        snapshot=json.loads(Path(state).read_text())
                        if time.time()-snapshot['updated_at']<10:
                            cached=[v for v in snapshot['gpus'] if v['uuid'] in selected]
                    except (OSError,ValueError,KeyError):pass
                if cached:
                    self._foreign=any(v['foreign_pids'] and (v['foreign_utilization'] is None or v['foreign_utilization']>0) for v in cached)
                else:
                    samples=NvidiaSmiProbe(process_utilization=True)()
                    self._foreign=any(v.foreign_pids and (v.foreign_utilization is None or v.foreign_utilization>0)
                                      for u,v in samples.items() if u in selected)
            except Exception:
                self._foreign = True
            self._last_probe = now
        if self._foreign:
            tick=time.monotonic()
            deadline = tick + compute_seconds * (1 / .3 - 1)
            self.progress(stage='shared_gpu_yield')
            while time.monotonic() < deadline:
                self.check_cancel()
                time.sleep(min(.25, max(.001, deadline - time.monotonic())))
            self.timings['shared_gpu_yield_seconds']=self.timings.get('shared_gpu_yield_seconds',0.)+time.monotonic()-tick


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args = parser.parse_args(argv)
    ctx = WorkerContext(args.request)
    def terminate(signum, frame):
        raise InferenceCancelled('Comparison worker terminated')
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        if sys.platform.startswith('linux') and ctx.request.get('parent_pid'):
            import ctypes
            if ctypes.CDLL(None).prctl(1, signal.SIGTERM) != 0:
                raise RuntimeError('Could not establish worker parent-death cleanup')
            if os.getppid() != ctx.request['parent_pid']:
                raise InferenceCancelled('Owning comparison service exited before worker startup')
        from .registry import backend_spec
        selection = {key: ctx.spec[key] for key in ('checkpoint', 'config') if ctx.spec.get(key)}
        current = backend_spec(ctx.request['algorithm'], **selection)
        if current['version'] != ctx.spec['version']:
            raise ValueError('Native source, model assets or comparison adapter changed; create a new run')
        if file_identity(ctx.sample['source']['path']) != ctx.sample['source']:
            raise ValueError('Source video changed after sample preparation')
        root = Path(ctx.spec['root'])
        from .read_only import configure_native_cache,protect_native_roots
        from .registry import root_for
        configure_native_cache(ctx.directory/'runtime')
        protect_native_roots([root_for('ace_kfree'),root_for('hawor_full')])
        os.chdir(root)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        os.environ.setdefault('MINT_MANO_DEVICE', 'cpu')
        import importlib.metadata
        versions = {}
        for package in ('numpy', 'torch', 'decord', 'smplx', 'transformers', 'diffusers', 'pytorch3d'):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                pass
        ctx.metadata['native_environment'] = {'python': sys.version, 'executable': sys.executable, 'packages': versions}
        if not ctx.request.get('import') and not ctx.request.get('external_pool'):
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError('No CUDA GPU assigned to the native inference worker')
            if not ctx.request.get('dynamic_gpus'):
                torch.cuda.set_per_process_memory_fraction(ctx.spec['memory_fraction'], 0)
            torch.cuda.reset_peak_memory_stats()
        frames_path = Path(ctx.sample['frames_path'])
        with file_lock(frames_path.parent / '.active.pin', shared=True), file_lock(frames_path.with_suffix('.pin'),shared=True):
            if not ctx.request.get('import'):
                if file_identity(frames_path) != ctx.sample['frames_identity']:
                    raise ValueError('Prepared RGB cache changed')
                ctx.frames = np.load(frames_path, mmap_mode='r', allow_pickle=False)
            from . import native
            with __import__('contextlib').ExitStack() as stack:
                if ctx.request.get('import'):
                    arrays = native.import_native(ctx)
                else:
                    function = native.run_mint if ctx.request['algorithm'] == 'mint' else native.run_ace if ctx.request['algorithm'].startswith('ace') else native.run_hawor
                    arrays = function(ctx)
                ctx.check_cancel()
                ctx.progress(stage='validate_publish')
                # Recheck disk identities after long native calls, before publishing a cached result.
                if backend_spec(ctx.request['algorithm'], **selection)['version'] != ctx.spec['version']:
                    raise ValueError('Native dependencies changed during inference; result was not published')
                if file_identity(ctx.sample['source']['path']) != ctx.sample['source']:
                    raise ValueError('Source video changed during inference')
                if not ctx.request.get('import') and file_identity(frames_path) != ctx.sample['frames_identity']:
                    raise ValueError('Prepared RGB frames changed during inference')
                ctx.timings['worker_end_to_end_seconds'] = time.perf_counter() - ctx.started
                ctx.metadata['peak_rss_gib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
                if 'torch' in sys.modules and not ctx.request.get('import') and not ctx.request.get('external_pool'):
                    import torch
                    ctx.metadata['peak_gpu_allocated_gib'] = torch.cuda.max_memory_allocated() / 2**30
                record = publish_result(ctx.directory / 'result', arrays,
                    {'run_id': ctx.run_id, 'sample_id': ctx.sample['id'], 'algorithm': ctx.request['algorithm'],
                     'label': ctx.spec['label'], 'backend_version': ctx.spec['version'], 'options': ctx.options,
                     'provenance': ctx.spec['identity'], 'timings': ctx.timings, **ctx.metadata})
        status = 'partial' if ctx.metadata.get('partial') or record['frames'] != ctx.sample['frames'] else 'completed'
        atomic_json(ctx.directory / 'exit.json', {'status': status, 'result_frames': record['frames'],
                                                'completed_at': time.time(), 'metadata': ctx.metadata})
        return 0
    except BaseException as exc:
        traceback.print_exc()
        status = 'cancelled' if isinstance(exc, (InferenceCancelled, KeyboardInterrupt)) else 'failed'
        atomic_json(ctx.directory / 'exit.json', {'status': status, 'error': str(exc),
            'stage': ctx._last_stage, 'traceback': traceback.format_exc(), 'completed_at': time.time()})
        return 2 if status == 'cancelled' else 1
    finally:
        if ctx.frames is not None:
            ctx.frames._mmap.close()
        if sys.platform.startswith('linux') and os.getpgrp() == os.getpid():
            from mint.service import _owned_members
            from mint.inference.cache_budget import _token
            token = _token(os.getpid())
            if token:
                # Native data-loader children must not retain inherited GPU leases.
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                deadline = time.monotonic() + 2
                while True:
                    children = [(pid, created) for pid, created in _owned_members(os.getpid(), token) if pid != os.getpid()]
                    if not children:
                        break
                    signum = signal.SIGTERM if time.monotonic() < deadline else signal.SIGKILL
                    for pid, created in children:
                        if _token(pid) == created:
                            try:
                                os.kill(pid, signum)
                            except ProcessLookupError:
                                pass
                    if signum == signal.SIGKILL:
                        break
                    time.sleep(.05)


if __name__ == '__main__':
    raise SystemExit(main())
