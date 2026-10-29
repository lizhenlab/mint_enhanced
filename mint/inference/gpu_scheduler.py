"""Idle-GPU admission and an ordered, cancellable queue shared by inference calls.

No CUDA work happens in the monitor. GPU replicas belong to individual workers
and are created only after both telemetry admission and a cooperative file lease.
"""

from __future__ import annotations

import csv
import fcntl
import math
import os
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import Future, wait
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GpuSample:
    uuid: str
    free_mib: int
    total_mib: int
    utilization: int
    own_mib: int = 0
    foreign_pids: tuple[int, ...] = ()
    foreign_utilization: int | None = None


class NvidiaSmiProbe:
    """Read physical UUIDs, including foreign jobs that currently use 0% compute."""

    def __init__(self, run=None, own_pids=None, process_utilization=False):
        self.run = run or subprocess.check_output
        self.own_pids = {os.getpid()} if own_pids is None else set(own_pids)
        self.process_utilization = process_utilization
        if own_pids is None:
            try:
                for line in Path('/proc/self/status').read_text().splitlines():
                    if line.startswith('NSpid:'):
                        self.own_pids.update(map(int, line.split()[1:]))
            except OSError:
                pass

    def __call__(self) -> dict[str, GpuSample]:
        def query(flag):
            value = self.run(
                ['nvidia-smi', flag, '--format=csv,noheader,nounits'],
                text=True, timeout=5, stderr=subprocess.PIPE,
            )
            return list(csv.reader(value.splitlines(), skipinitialspace=True))

        cards = query('--query-gpu=index,uuid,memory.free,memory.total,utilization.gpu')
        processes = query('--query-compute-apps=gpu_uuid,pid,used_memory')
        own, foreign = {}, {}
        for row in processes:
            if not row:
                continue
            uuid, pid, memory = (part.strip() for part in row)
            pid = int(pid)
            if pid in self.own_pids:
                own[uuid] = own.get(uuid, 0) + int(memory)
            else:
                foreign.setdefault(uuid, set()).add(pid)
        process_sm = {}
        if self.process_utilization:
            physical_uuids = {int(row[0]): row[1].strip() for row in cards}
            try:
                output = self.run(['nvidia-smi', 'pmon', '-s', 'u', '-c', '1'],
                                  text=True, timeout=5, stderr=subprocess.PIPE)
                for line in output.splitlines():
                    values = line.split()
                    if not values or line.lstrip().startswith('#') or len(values) < 4:
                        continue
                    if values[0].isdigit() and values[1].isdigit():
                        index, pid = int(values[0]), int(values[1])
                        if pid not in self.own_pids and index in physical_uuids:
                            foreign.setdefault(physical_uuids[index], set()).add(pid)
                        if values[3].isdigit():
                            process_sm[(index, pid)] = int(values[3])
            except (OSError, subprocess.SubprocessError):
                pass  # fall back conservatively to aggregate load when not measuring ourselves
        samples = {}
        for row in cards:
            index, uuid, free, total, utilization = (part.strip() for part in row)
            pids = tuple(sorted(foreign.get(uuid, ())))
            sm = [process_sm.get((int(index), pid)) for pid in pids]
            foreign_utilization = min(100, sum(sm)) if sm and all(v is not None for v in sm) else None
            samples[uuid] = GpuSample(
                uuid, int(free), int(total), int(utilization), own.get(uuid, 0),
                pids, foreign_utilization,
            )
        if not samples:
            raise RuntimeError('nvidia-smi returned no GPU telemetry')
        return samples


class GpuLease:
    """Prevent two cooperating MINT processes from claiming one idle GPU at once."""

    def __init__(self, uuid: str):
        directory = Path('/tmp') / f'mint-gpu-leases-{os.getuid()}'
        directory.mkdir(mode=0o700, exist_ok=True)
        self.path = directory / (uuid.replace('/', '_') + '.lock')
        self.file = None

    def acquire(self) -> bool:
        if self.file is not None:
            return True
        stream = self.path.open('a')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            return False
        self.file = stream
        return True

    def release(self):
        if self.file is not None:
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()
            self.file = None


@dataclass
class _Job:
    item: object
    run: object
    cancelled: threading.Event
    future: Future = field(default_factory=Future)
    attempted: set = field(default_factory=set)
    active: bool = False


class IdleGpuScheduler:
    """One serial worker per GPU; monitoring wakes workers as cards become idle."""

    def __init__(self, devices: dict[str, str], factory, dispose, *, probe=None,
                 poll_seconds=5.0, min_free_mib=12000, max_utilization=10,
                 idle_release_seconds=30.0, retry_exceptions=(), lease_factory=GpuLease,
                 policy='idle', share_utilization=35, share_duty_cycle=0.3):
        if not devices or len(devices) > 8:
            raise ValueError('Idle scheduling requires between 1 and 8 visible CUDA GPUs')
        if (not math.isfinite(poll_seconds) or poll_seconds <= 0 or min_free_mib <= 0
                or not math.isfinite(idle_release_seconds) or idle_release_seconds <= 0):
            raise ValueError('GPU polling interval and minimum memory must be positive')
        self.devices = dict(devices)
        if policy not in {'idle', 'shared'} or not 0 < share_duty_cycle <= 1:
            raise ValueError('Invalid GPU scheduling policy or shared duty cycle')
        self.policy, self.share_utilization = policy, int(share_utilization)
        self.share_duty_cycle = float(share_duty_cycle)
        self.factory, self.dispose = factory, dispose
        self.probe = probe or NvidiaSmiProbe(process_utilization=policy == 'shared')
        self.poll_seconds, self.min_free_mib = float(poll_seconds), int(min_free_mib)
        self.max_utilization = int(max_utilization)
        self.idle_release_seconds = float(idle_release_seconds)
        self.retry_exceptions = tuple(retry_exceptions)
        self._cv = threading.Condition()
        self._closed = False
        self._queue = deque()
        self._samples = {}
        self._probe_error = None
        self._last_probe = None
        self._wake_monitor = threading.Event()
        self._leases = {d: lease_factory(u) for d, u in devices.items()}
        self._states = {d: {'resident': False, 'running': False, 'completed': 0,
                            'last_error': None, 'cooldown_until': 0.0}
                        for d in devices}
        self._peak_running = 0
        self._last_work = {d: 0.0 for d in devices}
        self._share_wait_until = {d: 0.0 for d in devices}
        self._threads = [threading.Thread(target=self._monitor, daemon=True,
                                          name='gpu-idle-monitor')]
        self._threads += [threading.Thread(target=self._worker, args=(d,), daemon=True,
                                           name=f'gpu-idle-{d}') for d in devices]
        for thread in self._threads:
            thread.start()

    def _reason(self, device):
        if self._probe_error or self._last_probe is None:
            return 'telemetry unavailable'
        if time.monotonic() - self._last_probe > max(15, self.poll_seconds * 3):
            return 'telemetry stale'
        sample = self._samples.get(self.devices[device])
        if sample is None:
            return 'GPU UUID missing from telemetry'
        if sample.foreign_pids and self.policy == 'idle':
            return 'foreign compute process'
        if sample.free_mib + sample.own_mib < self.min_free_mib:
            return 'insufficient memory'
        if sample.own_mib == 0 and sample.utilization > self.max_utilization:
            if not sample.foreign_pids or self.policy == 'idle':
                return 'GPU utilization above idle threshold'
        if sample.foreign_pids and self.policy == 'shared':
            utilization = sample.foreign_utilization
            if utilization is None:
                if (self._states[device]['running'] or
                        time.monotonic() - self._last_work[device] < self.poll_seconds + 1):
                    return 'waiting for foreign load sample'
                utilization = sample.utilization
            if utilization > self.share_utilization:
                return 'foreign utilization above sharing threshold'
            if time.monotonic() < self._share_wait_until[device]:
                return 'shared duty-cycle pause'
        if time.monotonic() < self._states[device]['cooldown_until']:
            return 'retry cooldown'
        return None

    def _monitor(self):
        previous = None
        while True:
            with self._cv:
                if self._closed:
                    return
            try:
                samples, error = self.probe(), None
            except Exception as exc:  # a failed query never admits a GPU
                samples, error = {}, f'{type(exc).__name__}: {exc}'
            with self._cv:
                self._samples, self._probe_error = samples, error
                self._last_probe = time.monotonic()
                signature = tuple((d, self._reason(d)) for d in self.devices)
                if signature != previous:
                    print('[gpu-scheduler] ' + '; '.join(
                        f'{d}: {reason or "available"}' for d, reason in signature), flush=True)
                    previous = signature
                self._cv.notify_all()
            self._wake_monitor.wait(self.poll_seconds)
            self._wake_monitor.clear()

    def refresh(self):
        """Request a new telemetry snapshot without blocking an HTTP caller."""
        self._wake_monitor.set()

    def _dispose(self, resource, device):
        try:
            self.dispose(resource, device)
        except Exception as exc:
            print(f'[gpu-scheduler] cleanup on {device}: {exc}', flush=True)

    def _worker(self, device):
        resource = None
        lease = self._leases[device]
        try:
            while True:
                with self._cv:
                    if self._closed:
                        break
                    reason = self._reason(device)
                    job = None
                    if not reason:
                        for candidate in list(self._queue):
                            if candidate.cancelled.is_set() or candidate.future.cancelled():
                                self._queue.remove(candidate)
                                candidate.future.cancel()
                            elif device not in candidate.attempted:
                                job = candidate
                                self._queue.remove(candidate)
                                job.active = True
                                break
                    soft = reason in {'shared duty-cycle pause', 'waiting for foreign load sample'}
                    if (resource is not None and job is None and (not reason or soft)
                            and time.monotonic() - self._last_work[device] >= self.idle_release_seconds):
                        reason = 'replica idle timeout'
                        soft = False
                    if job is None and not (reason and not soft and resource is not None):
                        self._cv.wait(min(self.poll_seconds, 0.5))
                        continue
                if reason:
                    self._dispose(resource, device)
                    resource = None
                    lease.release()
                    with self._cv:
                        self._states[device]['resident'] = False
                    continue
                if not lease.acquire():
                    with self._cv:
                        job.active = False
                        self._queue.appendleft(job)
                        self._cv.notify_all()
                        self._cv.wait(min(self.poll_seconds, 0.5))
                    continue
                # Refresh before allocating a newly resident model, then recheck.
                if resource is None:
                    with self._cv:
                        stamp = self._last_probe
                    self.refresh()
                    with self._cv:
                        self._cv.wait_for(lambda: self._closed or self._last_probe != stamp,
                                          timeout=12)
                        if self._closed or self._reason(device) or job.cancelled.is_set():
                            job.active = False
                            self._queue.appendleft(job)
                            lease.release()
                            self._cv.notify_all()
                            continue
                requeue = False
                try:
                    with self._cv:
                        self._states[device]['running'] = True
                        self._peak_running = max(self._peak_running, sum(
                            s['running'] for s in self._states.values()))
                    if resource is None:
                        resource = self.factory(device)
                        with self._cv:
                            self._states[device]['resident'] = True
                    with self._cv:
                        unavailable = self._reason(device)
                    if unavailable and unavailable not in {'shared duty-cycle pause', 'waiting for foreign load sample'}:
                        # Another process may arrive while the model is being copied.
                        requeue = True
                        self._dispose(resource, device)
                        resource = None
                        lease.release()
                        with self._cv:
                            self._states[device]['resident'] = False
                        continue
                    if job.cancelled.is_set():
                        job.future.cancel()
                        continue
                    forward_started = time.monotonic()
                    value = job.run(resource, device, job.item)
                    forward_seconds = time.monotonic() - forward_started
                    job.future.set_result(value)
                    with self._cv:
                        self._states[device]['completed'] += 1
                        self._states[device]['last_error'] = None
                        self._last_work[device] = time.monotonic()
                        if self.policy == 'shared':
                            self._share_wait_until[device] = time.monotonic() + forward_seconds * (1 / self.share_duty_cycle - 1)
                except Exception as exc:
                    retry = isinstance(exc, self.retry_exceptions)
                    if retry:
                        exc.__traceback__ = None
                    job.attempted.add(device)
                    self._dispose(resource, device)
                    resource = None
                    lease.release()
                    with self._cv:
                        self._states[device].update(resident=False, last_error=str(exc),
                            cooldown_until=time.monotonic() + max(1, self.poll_seconds))
                        if retry and len(job.attempted) < len(self.devices) and not job.cancelled.is_set():
                            requeue = True
                        else:
                            job.future.set_exception(exc)
                    self.refresh()
                finally:
                    with self._cv:
                        job.active = False
                        self._states[device]['running'] = False
                        if requeue:
                            self._queue.append(job)
                        self._cv.notify_all()
        finally:
            if resource is not None:
                self._dispose(resource, device)
            lease.release()
            with self._cv:
                self._states[device].update(resident=False, running=False)
                self._cv.notify_all()

    def iter_map(self, items, run, *, cancel_check=None, cancelled_error=RuntimeError,
                 max_inflight=None):
        """Continuously refill a bounded queue and yield results in original order."""
        limit = int(max_inflight or max(4, len(self.devices) * 4))
        if limit < 1:
            raise ValueError('max_inflight must be positive')
        cancelled = threading.Event()
        source = iter(items)
        jobs, exhausted = deque(), False
        def fill():
            nonlocal exhausted
            with self._cv:
                if self._closed:
                    raise cancelled_error('GPU scheduler is closed')
                while len(jobs) < limit and not exhausted:
                    try:
                        item = next(source)
                    except StopIteration:
                        exhausted = True
                        break
                    job = _Job(item, run, cancelled)
                    jobs.append(job)
                    self._queue.append(job)
                self._cv.notify_all()
        try:
            fill()
            while jobs:
                if cancel_check is not None and cancel_check():
                    raise cancelled_error('Cancelled while waiting for GPU work')
                with self._cv:
                    if self._closed:
                        raise cancelled_error('GPU scheduler is closed')
                for job in jobs:
                    if job.future.done() and not job.future.cancelled() and job.future.exception() is not None:
                        raise job.future.exception()
                first = jobs[0]
                if not first.future.done():
                    # Wait on the head; already-completed later results stay within the queue budget.
                    wait([first.future], timeout=.1)
                    continue
                value = first.future.result()
                with self._cv:
                    self._cv.wait_for(lambda: not first.active)
                jobs.popleft()
                fill()
                yield value
        finally:
            cancelled.set()
            with self._cv:
                self._queue = deque(job for job in self._queue if job.cancelled is not cancelled)
                for job in jobs:
                    if not job.active and not job.future.done():
                        job.future.cancel()
                self._cv.notify_all()
                self._cv.wait_for(lambda: not any(job.active for job in jobs))

    def map(self, items, run, *, cancel_check=None, cancelled_error=RuntimeError):
        return list(self.iter_map(items, run, cancel_check=cancel_check, cancelled_error=cancelled_error))

    def status(self):
        with self._cv:
            cards = []
            for device, uuid in self.devices.items():
                sample = self._samples.get(uuid)
                cards.append({'device': device, 'uuid': uuid, 'reason': self._reason(device),
                              'free_mib': sample.free_mib if sample else None,
                              'own_mib': sample.own_mib if sample else None,
                              'foreign_pids': list(sample.foreign_pids) if sample else [],
                              'foreign_utilization': sample.foreign_utilization if sample else None,
                              **{k: v for k, v in self._states[device].items() if k != 'cooldown_until'}})
            return {'policy': self.policy, 'poll_seconds': self.poll_seconds,
                    'share_utilization': self.share_utilization,
                    'share_duty_cycle': self.share_duty_cycle,
                    'idle_release_seconds': self.idle_release_seconds,
                    'max_workers': len(self.devices), 'min_free_mib': self.min_free_mib,
                    'queued': len(self._queue), 'probe_error': self._probe_error,
                    'peak_running_workers': self._peak_running,
                    'eligible_devices': [c['device'] for c in cards if c['reason'] is None],
                    'resident_devices': [c['device'] for c in cards if c['resident']],
                    'running_devices': [c['device'] for c in cards if c['running']],
                    'gpus': cards}

    def close(self):
        with self._cv:
            self._closed = True
            for job in self._queue:
                job.future.cancel()
            self._queue.clear()
            self._cv.notify_all()
        self.refresh()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join()
