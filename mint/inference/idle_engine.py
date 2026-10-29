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
        scheduler = getattr(self, '_gpu_scheduler', None)
        if scheduler is not None:
            scheduler.close()
        pool = getattr(self, '_forward_pool', None)
        if pool is not None:
            pool.shutdown(wait=True)

    def predict(self, frames_uint8, on_step=None, cancel_check=None,
                cam_mode='chunked', window_batch_size=None, hand_mode='hard',
                preprocessed=False):
        scheduler = getattr(self, '_gpu_scheduler', None)
        kwargs = dict(on_step=on_step, cancel_check=cancel_check, cam_mode=cam_mode,
                      window_batch_size=window_batch_size, hand_mode=hand_mode,
                      preprocessed=preprocessed)
        if scheduler is None:
            return self._predict(frames_uint8, **kwargs)
        cancelled_error = self._inference_cancelled
        self._scheduling_context.cancel_check = cancel_check
        self._scheduling_context.cancelled_error = cancelled_error
        try:
            if cam_mode == 'streaming':
                result = scheduler.map(
                    [None], lambda view, device, _: view._predict(frames_uint8, **kwargs),
                    cancel_check=cancel_check, cancelled_error=cancelled_error,
                )[0]
            else:
                result = self._predict(frames_uint8, **kwargs)
            status = scheduler.status()
            result.setdefault('_timings', {})['gpu_scheduler'] = status
            result['_timings']['devices'] = status['resident_devices']
            return result
        finally:
            self._scheduling_context.cancel_check = None

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
