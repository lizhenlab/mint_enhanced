"""Low-rate host and physical-GPU telemetry; no framework import or CUDA context."""
from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import asdict
from pathlib import Path

from mint.inference.gpu_scheduler import NvidiaSmiProbe


def describe_progress(progress):
    """Explain a zero counter without pretending a segment has finished."""
    value = dict(progress)
    scheduler = value.get('gpu_scheduler') or {}
    gpus = scheduler.get('gpus') or []
    loading = [g for g in gpus if g.get('running') and not g.get('resident')]
    running = [g for g in gpus if g.get('running') and g.get('resident')]
    if value.get('detail'):
        return value
    if loading:
        value['detail'] = f'{len(loading)} 张 GPU 正在加载原生模型；加载完成后开始计算片段'
    elif running and not value.get('done') and value.get('stage') in {'parallel_inference', 'inference'}:
        value['detail'] = f'{len(running)} 张 GPU 正在计算首批片段；完整窗口/片段完成后更新计数'
    elif gpus and not running:
        reasons = sorted({str(g['reason']) for g in gpus if g.get('reason')})
        if reasons:
            value['detail'] = 'GPU 准入 / 让步：' + '；'.join(reasons[:4])
    return value


class ResourceMonitor:
    def __init__(self, root):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._value = {}
        self._at = 0
        self._previous_io = None

    def snapshot(self):
        with self._lock:
            now = time.time()
            if now - self._at < 5:
                return self._value
            disk = shutil.disk_usage(self.root)
            memory = {}
            try:
                memory = {line.split(':', 1)[0]: int(line.split()[1]) * 1024 for line in
                          Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:', 'MemAvailable:', 'SwapFree:'))}
            except OSError:
                pass
            io = {}
            try:
                raw = {line.split(':', 1)[0]: int(line.split(':', 1)[1]) for line in Path('/proc/self/io').read_text().splitlines()}
                if self._previous_io:
                    before, at = self._previous_io
                    io = {key + '_per_second': max(0, raw[key] - before[key]) / max(.01, now - at) for key in ('read_bytes', 'write_bytes')}
                self._previous_io = raw, now
            except OSError:
                pass
            try:
                gpus = [asdict(v) for v in NvidiaSmiProbe()().values()]
                gpu_error = None
            except Exception as error:
                gpus, gpu_error = [], str(error)
            self._value = {'at': now, 'cpu_count': os.cpu_count(), 'load_average': list(os.getloadavg()),
                'memory': memory, 'disk': {'free_bytes': disk.free, 'total_bytes': disk.total},
                'web_process_io': io, 'gpus': gpus, 'gpu_error': gpu_error,
                'scope': 'Host CPU/RAM/disk; physical GPUs include all users; IO is this web process only'}
            self._at = now
            return self._value
