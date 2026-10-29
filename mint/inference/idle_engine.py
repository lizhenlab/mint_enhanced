"""Shared idle-GPU integration for the standalone and production Viewer engines."""

from __future__ import annotations

import copy
import os
import threading
import time

import numpy as np

from .gpu_scheduler import IdleGpuScheduler


class IdleGpuMixin:
    def _initialize_idle_scheduler(self, model, devices, policy='idle'):
        import torch

        os.environ.setdefault('MINT_MANO_DEVICE', 'cpu')

        max_workers = int(os.environ.get('MINT_GPU_MAX_WORKERS', '8'))
        if not 1 <= max_workers <= 8:
            raise ValueError('MINT_GPU_MAX_WORKERS must be between 1 and 8')
        devices = devices[:max_workers]
        if any(d.type != 'cuda' for d in devices):
            raise RuntimeError('Idle GPU scheduling needs CUDA; use --devices cpu for CPU inference')
        if self.compile_mode is not None or self.fp8_mode is not None:
            raise ValueError('Idle scheduling requires compile/FP8 off; use --devices auto for fixed replicas')
        mapping = {}
        memory_fraction = float(os.environ.get('MINT_GPU_MEMORY_FRACTION', '0.30'))
        if not 0 < memory_fraction <= 1:
            raise ValueError('MINT_GPU_MEMORY_FRACTION must be in (0, 1]')
        for device in devices:
            uuid = str(getattr(torch.cuda.get_device_properties(device), 'uuid', ''))
            if not uuid or uuid.startswith('MIG-'):
                raise RuntimeError('Idle GPU scheduling needs a physical CUDA GPU UUID (MIG is not supported)')
            mapping[str(device)] = uuid if uuid.startswith('GPU-') else 'GPU-' + uuid
        self.model = model.cpu().eval()
        self.device = torch.device('cpu')
        self.models, self.devices = [self.model], []
        self._model_locks, self._forward_pool = [threading.Lock()], None
        self._fp8_conversions, self._compiled_module_names = [], []
        self.has_hand = bool(getattr(self.model, 'enable_hand', False))
        self.has_hand_presence = bool(getattr(self.model, 'enable_hand_presence', False))
        self._scheduling_context = threading.local()

        def factory(device):
            view = copy.copy(self)
            view._gpu_scheduler = None
            view.device = torch.device(device)
            replica = copy.deepcopy(self.model)
            try:
                with torch.cuda.device(view.device):
                    if policy == 'shared':
                        torch.cuda.set_per_process_memory_fraction(memory_fraction, view.device)
                    replica = replica.to(view.device).eval()
            except Exception:
                del replica
                with torch.cuda.device(view.device):
                    torch.cuda.empty_cache()
                raise
            view.model = replica
            view.models, view.devices = [replica], [view.device]
            view._model_locks, view._forward_pool = [threading.Lock()], None
            return view

        def dispose(view, device):
            if view is not None:
                view.models, view.model = [], None
            with torch.cuda.device(device):
                torch.cuda.empty_cache()

        self._gpu_scheduler = IdleGpuScheduler(
            mapping, factory, dispose,
            poll_seconds=float(os.environ.get('MINT_GPU_POLL_SECONDS', '5')),
            min_free_mib=int(os.environ.get('MINT_GPU_MIN_FREE_MIB', '16000' if policy == 'shared' else '12000')),
            idle_release_seconds=float(os.environ.get('MINT_GPU_IDLE_RELEASE_SECONDS', '30')),
            retry_exceptions=(torch.cuda.OutOfMemoryError,),
            policy=policy,
            share_utilization=int(os.environ.get('MINT_GPU_SHARE_UTILIZATION', '35')),
            share_duty_cycle=float(os.environ.get('MINT_GPU_SHARE_DUTY_CYCLE', '0.30')),
        )
        print(f'[gpu-scheduler] monitoring {len(mapping)} GPUs; replicas load on demand', flush=True)

    @property
    def gpu_scheduler_status(self):
        scheduler = getattr(self, '_gpu_scheduler', None)
        if scheduler is None:
            return {'policy': 'static'}
        return {**scheduler.status(), 'memory_fraction': float(os.environ.get('MINT_GPU_MEMORY_FRACTION', '0.30'))}

    def close(self):
        if getattr(self, '_closed', False):
            return
        self._closed = True
        scheduler = getattr(self, '_gpu_scheduler', None)
        if scheduler is not None:
            scheduler.close()
            self._gpu_scheduler = None
        pool = getattr(self, '_forward_pool', None)
        if pool is not None:
            pool.shutdown(wait=True)
            self._forward_pool = None
        devices = list(getattr(self, 'devices', []))
        self.models, self.model = [], None
        if devices:
            import torch
            for device in devices:
                if device.type == 'cuda':
                    with torch.cuda.device(device):
                        torch.cuda.empty_cache()

    def predict(self, frames_uint8, on_step=None, cancel_check=None,
                cam_mode='chunked', window_batch_size=None, hand_mode='hard',
                preprocessed=False, cache_identity=None, task_parameters=None, resume=True):
        scheduler = getattr(self, '_gpu_scheduler', None)
        if getattr(self, '_closed', False):
            raise RuntimeError('The inference engine has been closed')
        kwargs = dict(on_step=on_step, cancel_check=cancel_check, cam_mode=cam_mode,
                      window_batch_size=window_batch_size, hand_mode=hand_mode,
                      preprocessed=preprocessed, cache_identity=cache_identity,
                      task_parameters=task_parameters, resume=resume)
        cancelled_error = self._inference_cancelled
        if not hasattr(self, "_scheduling_context"):
            self._scheduling_context = threading.local()
        if not hasattr(self, "_prediction_context"):
            self._prediction_context = threading.local()
        self._prediction_context.task = None
        self._scheduling_context.cancel_check = cancel_check
        self._scheduling_context.cancelled_error = cancelled_error
        try:
            if scheduler is not None and cam_mode == 'streaming':
                result = scheduler.map(
                    [None], lambda view, device, _: view._predict(frames_uint8, **kwargs),
                    cancel_check=cancel_check, cancelled_error=cancelled_error,
                )[0]
            else:
                result = self._predict(frames_uint8, **kwargs)
            if scheduler is not None:
                status = scheduler.status()
                result.setdefault('_timings', {})['gpu_scheduler'] = status
                result['_timings']['devices'] = status['resident_devices']
            task = self._prediction_context.task
            if task is not None:
                task.finish('inference_complete', stage='inference_complete')
                result['_task'] = task.metadata
            return result
        except Exception as exc:
            task = self._prediction_context.task
            if task is not None:
                task.finish('cancelled' if isinstance(exc, cancelled_error) else 'failed', error=str(exc))
            raise
        finally:
            self._scheduling_context.cancel_check = None
            task = self._prediction_context.task
            if task is not None:
                task.close()
                from .checkpoints import prune_tasks
                prune_tasks(task.directory)

    def _idle_forward_window_batch(self, x, bounds, disable_persistent_kv_cache):
        started = time.perf_counter()
        def run(view, device, bound):
            return view._forward_window_batch(
                x, [bound], disable_persistent_kv_cache=disable_persistent_kv_cache,
            )[0]
        outputs = self._gpu_scheduler.map(
            bounds, run, cancel_check=self._scheduling_context.cancel_check,
            cancelled_error=self._scheduling_context.cancelled_error,
        )
        keys = set.intersection(*(set(output) for output in outputs))
        return {key: np.concatenate([output[key] for output in outputs], axis=0)
                for key in keys}, time.perf_counter() - started

    def _iter_forward_batches(self, x, bounds, batch_size, cancel_check, checkpoint=None, resume=True):
        """Feed fast GPUs continuously while the caller stitches strictly in input order."""
        cached = {}
        if checkpoint is not None and resume:
            for index in range(len(bounds)):
                value = checkpoint.load(index)
                if value is not None:
                    cached[index] = value
        scheduler = getattr(self, '_gpu_scheduler', None)
        if scheduler is not None:
            missing = [(index, bound) for index, bound in enumerate(bounds) if index not in cached]
            def forward(view, device, item):
                index, bound = item
                output, seconds = view._forward_window_batch(x, [bound], disable_persistent_kv_cache=True)
                if checkpoint is not None:
                    checkpoint.save(index, output)
                return index, output, seconds
            results = scheduler.iter_map(missing, forward, cancel_check=cancel_check,
                cancelled_error=self._inference_cancelled,
                max_inflight=int(os.environ.get('MINT_GPU_PREFETCH_WINDOWS', str(len(scheduler.devices) * 4))))
            try:
                for index, bound in enumerate(bounds):
                    if cancel_check is not None and cancel_check():
                        raise self._inference_cancelled('Window inference cancelled')
                    if index in cached:
                        output, seconds = cached.pop(index), 0.0
                    else:
                        actual, output, seconds = next(results)
                        if actual != index:
                            raise RuntimeError('GPU queue returned an unordered window')
                    yield [bound], output, seconds, index + 1
            finally:
                results.close()
            return
        index = 0
        while index < len(bounds):
            if cancel_check is not None and cancel_check():
                raise self._inference_cancelled('Window inference cancelled')
            if index in cached:
                yield [bounds[index]], cached.pop(index), 0.0, index + 1
                index += 1
                continue
            end = min(len(bounds), index + batch_size)
            for candidate in range(index + 1, end):
                if candidate in cached or bounds[candidate][1] - bounds[candidate][0] != bounds[index][1] - bounds[index][0]:
                    end = candidate
                    break
            import torch
            try:
                output, seconds = self._forward_window_batch(x, bounds[index:end], disable_persistent_kv_cache=True)
            except torch.cuda.OutOfMemoryError:
                if end - index <= 1:
                    raise
                batch_size = max(1, (end - index) // 2)
                continue
            if checkpoint is not None:
                for offset, window in enumerate(range(index, end)):
                    checkpoint.save(window, {key: value[offset:offset+1] for key, value in output.items()})
            yield bounds[index:end], output, seconds, end
            index = end
