"""Dynamic physical-GPU leases around isolated, persistent native processes."""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import threading
import time
from pathlib import Path

from mint.inference.base import InferenceCancelled
from mint.inference.cache_budget import _token
from mint.inference.cache_budget import file_lock
from mint.runtime import atomic_json
from mint.inference.gpu_scheduler import GpuLease, IdleGpuScheduler, NvidiaSmiProbe
from .registry import PROJECT


class NativeGPUOOM(RuntimeError):
    pass


class NativeMemoryLease:
    """Reserve replica RAM across cooperating jobs before cold model loads."""
    def __init__(self, directory, key, gib):
        self.directory, self.key = Path(directory), key
        self.bytes = int(gib * 2**30)
        self.path = self.directory / 'native-memory.json'

    def update(self, acquire):
        if not Path('/proc/meminfo').exists():
            return True
        with file_lock(self.directory/'native-memory.lock'):
            try: values=json.loads(self.path.read_text())
            except (OSError,ValueError): values={}
            values={key:value for key,value in values.items() if _token(value['pid'])==value['token']}
            accepted=True
            if acquire and self.key not in values:
                available=next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
                               if line.startswith('MemAvailable:'))
                reserve=int(float(os.environ.get('MINT_NATIVE_RAM_RESERVE_GIB','64'))*2**30)
                accepted=available-sum(v['bytes'] for v in values.values())-self.bytes>=reserve
                if accepted: values[self.key]={'pid':os.getpid(),'token':_token(os.getpid()),'bytes':self.bytes}
            elif not acquire:
                values.pop(self.key,None)
            atomic_json(self.path,values)
            return accepted


class NativeProcess:
    def __init__(self, pool, device):
        self.pool, self.device, self.child = pool, device, None
        self.closed = False
        self.close_lock = threading.Lock()
        self.buffer = b''
        read_fd, write_fd = os.pipe()
        self.read_fd = read_fd
        env = os.environ.copy()
        env.update(PYTHONPATH=str(PROJECT), CUDA_VISIBLE_DEVICES=pool.devices[device],
                   PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2',
                   MINT_NATIVE_GPU_STATE=str(pool.ctx.work/(pool.kind+'-gpu-state.json')))
        command = [pool.ctx.spec['python'], '-m', 'mint.comparison.native_process',
                   '--request', str(pool.ctx.path), '--kind', pool.kind, '--owner', str(os.getpid()),
                   '--reply-fd', str(write_fd)]
        lease = pool.scheduler._leases[device]
        inherited = (write_fd, lease.file.fileno())
        try:
            with (pool.ctx.work / f'{pool.kind}-{device.replace(":", "-")}.log').open('ab', buffering=0) as log:
                self.child = subprocess.Popen(command, cwd=PROJECT, env=env, stdin=subprocess.PIPE,
                    stdout=log, stderr=subprocess.STDOUT, close_fds=True, pass_fds=inherited)
            os.close(write_fd); write_fd = None
            self.token = _token(self.child.pid)
            with pool.lock:
                pool.processes[device] = self
            ready = self.receive(240)
            if ready.get('type') != 'ready':
                self.raise_error(ready)
            self.ready = ready
            with pool.lock:pool.model_load_seconds[device]=ready.get('model_load_seconds',0.)
        except BaseException:
            self.close(); raise
        finally:
            if write_fd is not None:
                os.close(write_fd)

    @staticmethod
    def raise_error(value):
        error = NativeGPUOOM if value.get('retryable') else RuntimeError
        raise error(value.get('error', 'Native process did not become ready'))

    def receive(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            self.pool.ctx.check_cancel()
            if self.pool.closing.is_set():
                raise InferenceCancelled('Native pool is stopping')
            if b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                return json.loads(line)
            if self.child.poll() is not None:
                raise RuntimeError(f'{self.pool.kind} worker exited {self.child.returncode}; see per-GPU log')
            if time.monotonic() > deadline:
                raise TimeoutError(f'{self.pool.kind} native worker reply timed out')
            if select.select([self.read_fd], [], [], .2)[0]:
                data = os.read(self.read_fd, 65536)
                if not data:
                    raise RuntimeError('Native reply pipe closed')
                self.buffer += data

    def call(self, item):
        self.pool.ctx.check_cancel()
        self.child.stdin.write((json.dumps(item) + '\n').encode()); self.child.stdin.flush()
        value = self.receive(float(os.environ.get('MINT_NATIVE_TASK_TIMEOUT_SECONDS', '3600')))
        if value.get('type') != 'result':
            self.raise_error(value)
        with self.pool.lock:
            self.pool.completed_devices.add(self.device)
            self.pool.native_seconds += value.get('seconds', 0.)
            self.pool.shared_yield_seconds += value.get('shared_yield_seconds',0.)
            self.pool.gpu_peaks[self.device] = max(self.pool.gpu_peaks.get(self.device, 0.), value.get('peak_gpu_gib', 0.))
        return value

    def close(self):
        with self.close_lock:
            if self.closed:
                return
            self.closed = True
        child = self.child
        if child is not None:
            if child.poll() is None and _token(child.pid) == getattr(self, 'token', _token(child.pid)):
                child.terminate()
                try: child.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    if _token(child.pid) == self.token: child.kill()
                    child.wait(timeout=5)
            if child.stdin is not None: child.stdin.close()
        os.close(self.read_fd)
        with self.pool.lock:
            if self.pool.processes.get(self.device) is self:
                self.pool.processes.pop(self.device, None)


class NativePool:
    def __init__(self, ctx, kind, *, maximum=None, min_free_mib=None):
        self.ctx, self.kind = ctx, kind
        self.maximum = int(maximum or ctx.options['max_gpus'])
        self.lock = threading.RLock()
        self.closing = threading.Event()
        self.processes, self.gpu_peaks, self.model_load_seconds = {}, {}, {}
        self.completed_devices = set()
        self.native_seconds = 0.
        self.shared_yield_seconds = 0.
        initial=NvidiaSmiProbe()()
        physical={f'gpu:{i}':uuid for i,uuid in enumerate(initial)}
        self.devices=dict(sorted(physical.items(),key=lambda value:(bool(initial[value[1]].foreign_pids),-initial[value[1]].free_mib,value[0])))
        if not self.devices:
            raise RuntimeError('No physical NVIDIA GPUs were found')
        held = set(); gate = threading.Lock()
        pool = self
        class CappedLease(GpuLease):
            def __init__(self,uuid):
                super().__init__(uuid)
                self.ram=NativeMemoryLease(self.path.parent,f'{os.getpid()}:{uuid}',40 if kind=='ace' else 8)
            def acquire(self):
                with gate:
                    if self.file is not None: return True
                    if len(held) >= pool.maximum: return False
                    if not super().acquire(): return False
                    if not self.ram.update(True):
                        super().release()
                        return False
                    held.add(self.path)
                    return True
            def release(self):
                with gate:
                    self.ram.update(False)
                    super().release(); held.discard(self.path)
        def probe():
            from mint.service import _owned_members
            token = _token(os.getpid())
            own = {os.getpid()}
            if token and os.getpgrp() == os.getpid():
                own.update(pid for pid, _ in _owned_members(os.getpid(), token))
            with self.lock:
                own.update(p.child.pid for p in self.processes.values())
            return NvidiaSmiProbe(own_pids=own, process_utilization=ctx.options['resource_policy']=='shared')()
        # Threads cannot submit work before self.scheduler has been assigned.
        self.scheduler = IdleGpuScheduler(self.devices, lambda d: NativeProcess(self, d), lambda p, d: p.close(),
            probe=probe, poll_seconds=2., min_free_mib=min_free_mib or ctx.spec['min_free_mib'],
            # Keep expensive native models through a shared-duty pause or a slow
            # neighbour's cold load. Hard memory/load pressure still releases them.
            idle_release_seconds=180., policy='shared' if ctx.options['resource_policy']=='shared' else 'idle',
            retry_exceptions=(NativeGPUOOM,), lease_factory=CappedLease)

    def run(self, items, stage='parallel_inference'):
        items = list(items)
        self.maximum = min(self.maximum, max(1, len(items)))
        started = time.perf_counter()
        done = [0]
        stop = threading.Event()
        def monitor():
            while not stop.wait(.75):
                if self.ctx.cancelled(): return
                status = self.scheduler.status()
                atomic_json(self.ctx.work/(self.kind+'-gpu-state.json'),{'updated_at':time.time(),'gpus':status['gpus']})
                self.ctx.progress(stage=stage, done=done[0], total=len(items),
                    gpu_scheduler={**status, 'max_workers': self.maximum}, native_kind=self.kind)
        thread = threading.Thread(target=monitor, daemon=True); thread.start()
        try:
            values = []
            for value in self.scheduler.iter_map(items, lambda process, device, item: process.call(item),
                    cancel_check=self.ctx.cancelled, cancelled_error=InferenceCancelled, max_inflight=max(32,self.maximum*8)):
                values.append(value); done[0] += 1
            return values
        finally:
            stop.set(); thread.join(timeout=2)
            status = self.scheduler.status()
            self.ctx.metadata.setdefault('parallel_stages', {})[self.kind] = {
                'wall_seconds': time.perf_counter()-started, 'native_worker_seconds': self.native_seconds,
                'shared_yield_worker_seconds':self.shared_yield_seconds,
                'gpu_devices': sorted(self.completed_devices), 'peak_running_workers': status['peak_running_workers'],
                'maximum_workers': self.maximum, 'completed_tasks': done[0], 'total_tasks': len(items),
                'peak_gpu_gib': self.gpu_peaks}
            self.ctx.metadata['parallel_stages'][self.kind]['model_load_seconds_by_device']=dict(self.model_load_seconds)

    def close(self):
        self.closing.set()
        with self.lock: processes = list(self.processes.values())
        for process in processes: process.close()
        self.scheduler.close()

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
