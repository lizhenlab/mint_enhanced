"""Execution-only acceleration of HaWoR's original masked DROID-SLAM.

The solver, eight-keyframe batches, iteration counts and frame order remain
those in DROID-SLAM. Only input delivery and lossless checkpoint I/O change;
the external implementation and its weights are never written to.
"""
from __future__ import annotations

import queue
import json
import os
import shutil
import tempfile
import threading
import time

from mint.runtime import atomic_json


def write_slam_geometry(path, **arrays):
    """Avoid single-core deflate on multi-GB depths when disk headroom permits."""
    import numpy as np
    from mint.runtime import atomic_npz
    required = sum(value.nbytes for value in arrays.values())+16*2**30
    disabled=os.environ.get('MINT_HAWOR_SLAM_ACCELERATE','1')=='0'
    if disabled or shutil.disk_usage(path.parent).free < required:
        atomic_npz(path, **arrays)
        return 'lossless compressed NPZ (native baseline)' if disabled else 'lossless compressed NPZ (disk reserve)'
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            np.savez(stream, **arrays)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        from pathlib import Path
        Path(temporary).unlink(missing_ok=True)
    return 'lossless uncompressed NPZ'


def read_slam_progress(ctx, started_at):
    try:
        value = json.loads((ctx.work/'hawor-slam-progress.json').read_text())
        stages = {'slam_tracking', 'slam_global_ba', 'slam_trajectory', 'slam_checkpoint'}
        if value['run_id'] == ctx.run_id and value['updated_at'] >= started_at and value['stage'] in stages:
            return {key: value[key] for key in ('stage', 'done', 'total', 'keyframes', 'elapsed_seconds', 'iteration', 'pass_steps') if key in value}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}


def prefetch_stream(stream, capacity=4):
    """Overlap the original CPU decoder with CUDA, with bounded RAM and cleanup."""
    pending = queue.Queue(maxsize=capacity)
    stopped = threading.Event()

    def put(value):
        while not stopped.is_set():
            try:
                pending.put(value, timeout=.1)
                return
            except queue.Full:
                pass

    def produce():
        try:
            import torch
            # Match NativeProcess's inference mode, which is thread-local.
            with torch.inference_mode():
                for value in stream:
                    if stopped.is_set():
                        break
                    put((True, value))
        except BaseException as exc:
            put((False, exc))
        finally:
            close = getattr(stream, 'close', None)
            if close:
                close()
            put(None)

    producer = threading.Thread(target=produce, name='hawor-image-prefetch', daemon=True)
    producer.start()
    try:
        while True:
            value = pending.get()
            if value is None:
                break
            ok, item = value
            if not ok:
                raise item
            yield item
    finally:
        stopped.set()
        producer.join(timeout=2)


class SlamProgress:
    def __init__(self, ctx):
        self.ctx = ctx
        self.started = time.perf_counter()
        self.last = 0.
        self.stage = None
        self.ba_steps = 0
        self.ba_seconds = 0.
        self.track_seconds = 0.
        self.fill_seconds = 0.
        self.index_cache_bytes = 0

    def __call__(self, stage, done=0, total=1, *, force=False, **values):
        self.ctx.check_cancel()
        now = time.perf_counter()
        if not force and self.stage == stage and now-self.last < 1:
            return
        self.last, self.stage = now, stage
        atomic_json(self.ctx.work/'hawor-slam-progress.json', {
            'run_id': self.ctx.run_id, 'stage': stage, 'done': done, 'total': total,
            'updated_at': time.time(), 'elapsed_seconds': now-self.started, **values})


def install_runtime(native, droid_module, report):
    """Overlap tracking decode; keep the original native solver and filler."""
    original_stream = native.image_stream
    enabled = os.environ.get('MINT_HAWOR_SLAM_ACCELERATE','1') != '0'
    def image_stream(*args, **kwargs):
        stream = original_stream(*args, **kwargs)
        # Prefetching the filler competes with its 16-frame CPU batching and
        # did not improve the measured 720-frame case. Keep that path native.
        if enabled and report.stage in (None, 'slam_tracking'):
            yield from prefetch_stream(stream)
        else:
            yield from stream
    native.image_stream = image_stream
    def restore():
        native.image_stream = original_stream
    return restore
