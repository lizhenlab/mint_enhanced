"""Scheduling behavior tests use fake telemetry; no GPUs or CUDA allocations needed."""

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from mint.inference.gpu_scheduler import GpuSample, IdleGpuScheduler, NvidiaSmiProbe


def eventually(check, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError('Condition did not become true')


class FakeLease:
    def __init__(self, uuid):
        pass

    def acquire(self):
        return True

    def release(self):
        pass


class Telemetry:
    def __init__(self, count, idle=()):
        self.count, self.idle = count, set(idle)
        self.fail = False

    def __call__(self):
        if self.fail:
            raise RuntimeError('telemetry failed')
        return {f'GPU-{i}': GpuSample(f'GPU-{i}', 40000, 40960, 0,
                    foreign_pids=() if i in self.idle else (999,))
                for i in range(self.count)}


class SchedulerTests(unittest.TestCase):
    def make_pool(self, telemetry, **kwargs):
        pool = IdleGpuScheduler({f'cuda:{i}': f'GPU-{i}' for i in range(telemetry.count)},
            kwargs.pop('factory', lambda device: device),
            kwargs.pop('dispose', lambda resource, device: None),
            probe=telemetry, poll_seconds=0.02, min_free_mib=12000,
            lease_factory=FakeLease, **kwargs)
        self.addCleanup(pool.close)
        return pool

    def test_probe_counts_foreign_jobs_even_with_zero_utilization(self):
        def run(command, **kwargs):
            if '--query-gpu=' in command[1]:
                return '0, GPU-a, 10000, 40960, 0\n1, GPU-b, 40000, 40960, 0\n'
            return 'GPU-a, 123, 30000\nGPU-b, 456, 1024\n'
        sample = NvidiaSmiProbe(run=run, own_pids={123})()
        self.assertEqual(sample['GPU-a'].own_mib, 30000)
        self.assertEqual(sample['GPU-a'].foreign_pids, ())
        self.assertEqual(sample['GPU-b'].foreign_pids, (456,))

    def test_process_load_excludes_our_own_gpu_work(self):
        def run(command, **kwargs):
            if command[1] == 'pmon':
                return '# gpu pid type sm mem\n0 123 C 90 5\n0 456 C 8 2\n'
            if '--query-gpu=' in command[1]:
                return '0, GPU-a, 20000, 40960, 100\n'
            return 'GPU-a, 123, 8000\nGPU-a, 456, 4000\n'
        sample = NvidiaSmiProbe(run=run, own_pids={123}, process_utilization=True)()['GPU-a']
        self.assertEqual(sample.foreign_utilization, 8)
        self.assertEqual(sample.foreign_pids, (456,))

    def test_shared_mode_admits_a_busy_gpu_with_headroom(self):
        telemetry = Telemetry(1)
        telemetry.__class__ = type('SharedMemory', (Telemetry,), {
            '__call__': lambda _: {'GPU-0': GpuSample('GPU-0', 35000, 40960, 100,
                                                     own_mib=8000, foreign_pids=(999,), foreign_utilization=8)}})
        pool = self.make_pool(telemetry, policy='shared')
        self.assertEqual(pool.map([1], lambda r, d, i: i), [1])

    def test_shared_mode_yields_to_high_foreign_load(self):
        telemetry = Telemetry(1)
        telemetry.__class__ = type('HighLoad', (Telemetry,), {
            '__call__': lambda _: {'GPU-0': GpuSample('GPU-0', 35000, 40960, 80,
                                                     foreign_pids=(999,), foreign_utilization=80)}})
        pool = self.make_pool(telemetry, policy='shared')
        eventually(lambda: pool.status()['gpus'][0]['reason'] == 'foreign utilization above sharing threshold')
        self.assertEqual(pool.status()['eligible_devices'], [])

    def test_shared_duty_cycle_adds_a_pause_between_windows(self):
        telemetry = Telemetry(1)
        telemetry.__class__ = type('LowLoad', (Telemetry,), {
            '__call__': lambda _: {'GPU-0': GpuSample('GPU-0', 35000, 40960, 8,
                                                     foreign_pids=(999,), foreign_utilization=8)}})
        pool = self.make_pool(telemetry, policy='shared', share_duty_cycle=0.5)
        started=[]
        def run(resource, device, item):
            started.append(time.monotonic())
            time.sleep(0.04)
            return item
        self.assertEqual(pool.map([1,2],run), [1,2])
        self.assertGreaterEqual(started[1]-started[0], 0.075)

    def test_eight_workers_really_run_concurrently_and_restore_order(self):
        telemetry = Telemetry(8, range(8))
        pool = self.make_pool(telemetry)
        barrier = threading.Barrier(8, timeout=3)
        devices, lock = [], threading.Lock()
        def run(resource, device, item):
            with lock:
                devices.append(device)
            barrier.wait()
            time.sleep((7 - item) * 0.002)
            return item * 10
        result = pool.map(range(8), run)
        self.assertEqual(result, [i * 10 for i in range(8)])
        self.assertEqual(len(set(devices)), 8)
        self.assertEqual(pool.status()['peak_running_workers'], 8)

    def test_new_idle_gpus_join_a_queue_already_in_progress(self):
        telemetry = Telemetry(8, {0})
        pool = self.make_pool(telemetry)
        started, gate, lock = set(), threading.Event(), threading.Lock()
        def run(resource, device, item):
            with lock:
                started.add(device)
            self.assertTrue(gate.wait(3))
            return item
        with ThreadPoolExecutor(1) as caller:
            result = caller.submit(pool.map, range(16), run)
            eventually(lambda: started == {'cuda:0'})
            telemetry.idle = set(range(8))
            pool.refresh()
            eventually(lambda: len(started) == 8)
            gate.set()
            self.assertEqual(result.result(3), list(range(16)))

    def test_all_busy_waits_without_loading_and_can_be_cancelled(self):
        telemetry = Telemetry(8)
        loaded = []
        pool = self.make_pool(telemetry, factory=lambda device: loaded.append(device))
        cancelled = threading.Event()
        with ThreadPoolExecutor(1) as caller:
            result = caller.submit(pool.map, range(8), lambda *_: None,
                                   cancel_check=cancelled.is_set)
            eventually(lambda: pool.status()['queued'] == 8)
            self.assertEqual(loaded, [])
            cancelled.set()
            with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
                result.result(2)
        self.assertEqual(pool.status()['queued'], 0)

    def test_foreign_job_parks_replica_then_reloads_when_it_leaves(self):
        telemetry = Telemetry(1, {0})
        loaded, disposed = [], threading.Event()
        pool = self.make_pool(telemetry,
            factory=lambda d: loaded.append(d) or d,
            dispose=lambda *_: disposed.set())
        first_started, finish_first = threading.Event(), threading.Event()
        def run(resource, device, item):
            if item == 0:
                first_started.set()
                self.assertTrue(finish_first.wait(3))
            return item
        with ThreadPoolExecutor(1) as caller:
            result = caller.submit(pool.map, [0, 1], run)
            self.assertTrue(first_started.wait(3))
            telemetry.idle = set()
            pool.refresh()
            eventually(lambda: pool.status()['gpus'][0]['reason'] == 'foreign compute process')
            finish_first.set()
            self.assertTrue(disposed.wait(3))
            self.assertFalse(result.done())
            telemetry.idle = {0}
            pool.refresh()
            self.assertEqual(result.result(3), [0, 1])
        self.assertEqual(len(loaded), 2)

    def test_probe_failure_does_not_admit_gpus(self):
        telemetry = Telemetry(1, {0})
        telemetry.fail = True
        loaded = []
        pool = self.make_pool(telemetry, factory=lambda d: loaded.append(d))
        eventually(lambda: pool.status()['probe_error'] is not None)
        self.assertEqual(pool.status()['eligible_devices'], [])
        self.assertEqual(loaded, [])

    def test_unused_resident_replicas_are_released(self):
        telemetry = Telemetry(1, {0})
        disposed = threading.Event()
        pool = self.make_pool(telemetry, idle_release_seconds=0.04,
                              dispose=lambda *_: disposed.set())
        self.assertEqual(pool.map([1], lambda r, d, i: i), [1])
        self.assertTrue(disposed.wait(2))
        self.assertEqual(pool.status()['resident_devices'], [])

    def test_foreign_job_arriving_during_model_loading_prevents_forward(self):
        telemetry = Telemetry(1, {0})
        loading, finish_loading, disposed = threading.Event(), threading.Event(), threading.Event()
        def factory(device):
            loading.set()
            self.assertTrue(finish_loading.wait(3))
            return device
        pool = self.make_pool(telemetry, factory=factory, dispose=lambda *_: disposed.set())
        forwarded = []
        with ThreadPoolExecutor(1) as caller:
            result = caller.submit(pool.map, [1], lambda r, d, i: forwarded.append(i) or i)
            self.assertTrue(loading.wait(3))
            telemetry.idle = set()
            pool.refresh()
            eventually(lambda: pool.status()['gpus'][0]['reason'] == 'foreign compute process')
            finish_loading.set()
            self.assertTrue(disposed.wait(3))
            self.assertEqual(forwarded, [])
            telemetry.idle = {0}
            pool.refresh()
            self.assertEqual(result.result(3), [1])

    def test_one_call_does_not_wait_for_another_calls_inflight_job(self):
        telemetry = Telemetry(2, {0, 1})
        pool = self.make_pool(telemetry)
        both_started = threading.Barrier(2, timeout=3)
        slow_can_finish = threading.Event()
        def run(resource, device, item):
            both_started.wait()
            if item == 'slow':
                self.assertTrue(slow_can_finish.wait(3))
            return item
        with ThreadPoolExecutor(2) as caller:
            fast = caller.submit(pool.map, ['fast'], run)
            slow = caller.submit(pool.map, ['slow'], run)
            try:
                self.assertEqual(fast.result(2), ['fast'])
                self.assertFalse(slow.done())
            finally:
                slow_can_finish.set()
            self.assertEqual(slow.result(2), ['slow'])

    def test_oom_is_retried_on_a_different_gpu(self):
        class OOM(RuntimeError):
            pass
        telemetry = Telemetry(2, {0})
        attempted = []
        def factory(device):
            attempted.append(device)
            if device == 'cuda:0':
                telemetry.idle = {0, 1}
                raise OOM('not enough memory')
            return device
        pool = self.make_pool(telemetry, factory=factory, retry_exceptions=(OOM,))
        self.assertEqual(pool.map([7], lambda r, d, i: (d, i)), [('cuda:1', 7)])
        self.assertEqual(attempted, ['cuda:0', 'cuda:1'])

    def test_terminal_error_does_not_leave_other_jobs_running(self):
        telemetry = Telemetry(2, {0, 1})
        pool = self.make_pool(telemetry)
        def run(resource, device, item):
            if item == 0:
                raise ValueError('invalid input')
            time.sleep(0.03)
            return item
        with self.assertRaisesRegex(ValueError, 'invalid input'):
            pool.map(range(10), run)
        self.assertEqual(pool.status()['queued'], 0)
        self.assertEqual(pool.status()['running_devices'], [])

    def test_close_wakes_callers_waiting_for_busy_gpus(self):
        telemetry = Telemetry(1)
        pool = self.make_pool(telemetry)
        with ThreadPoolExecutor(1) as caller:
            result = caller.submit(pool.map, [1], lambda *_: None)
            eventually(lambda: pool.status()['queued'] == 1)
            pool.close()
            with self.assertRaises(RuntimeError):
                result.result(2)

    def test_own_memory_is_available_and_foreign_memory_is_not(self):
        telemetry = Telemetry(1, {0})
        telemetry.__class__ = type('OwnMemory', (Telemetry,), {
            '__call__': lambda _: {'GPU-0': GpuSample('GPU-0', 1000, 40960, 100, own_mib=35000)}})
        pool = self.make_pool(telemetry)
        eventually(lambda: pool.status()['eligible_devices'] == ['cuda:0'])


if __name__ == '__main__':
    unittest.main()
