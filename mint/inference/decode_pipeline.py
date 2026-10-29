"""A demand-driven decoder feeds native RGB windows while inference is running."""
from __future__ import annotations

import os
import threading
import time
import numpy as np


class DecodedFrames:
    def __init__(self, shape, produce, *, cancel_check=None, cancelled_error=RuntimeError):
        self.shape, self.ndim, self.dtype = tuple(shape), 4, np.dtype('uint8')
        self._cv = threading.Condition()
        self._cancel = threading.Event()
        self._external_cancel, self._cancelled_error = cancel_check, cancelled_error
        self._frames = None
        self._buffers = []
        self._ready = self._demand = self._peak_ahead = 0
        self._done = self._drain = False
        self._error = None
        self._started = time.perf_counter()
        self._finished = self._first_read = None
        self._read_before_complete = False
        self._wait_seconds = 0.0
        per_frame = max(1, int(np.prod(shape[1:])))
        prefetch = int(os.environ.get('MINT_DECODE_PREFETCH_MIB', '128'))
        if prefetch <= 0:
            raise ValueError('MINT_DECODE_PREFETCH_MIB must be positive')
        self._ahead = max(32, min(512, prefetch * 2**20 // per_frame))
        def run():
            try:
                frames = produce(self)
                with self._cv:
                    self._frames = frames
                    self._ready = len(self)
                    self._done = True
                    self._finished = time.perf_counter()
                    self._cv.notify_all()
            except BaseException as exc:
                with self._cv:
                    self._error = exc
                    self._finished = time.perf_counter()
                    self._cv.notify_all()
        self._thread = threading.Thread(target=run, name='video-decode', daemon=True)
        self._thread.start()

    def __len__(self):
        return self.shape[0]

    @property
    def filename(self):
        return getattr(self._frames, 'filename', None)

    @property
    def _mmap(self):
        return getattr(self._frames, '_mmap', None) if self._done else None

    @property
    def failed(self):
        return self._error is not None or (not self._done and self.cancelled())

    def cancelled(self):
        return self._cancel.is_set() or bool(not self._done and self._external_cancel and self._external_cancel())

    def _check(self):
        if self._error is not None:
            raise self._error
        if self.cancelled():
            raise self._cancelled_error('Video decoding cancelled')

    def bind(self, frames):
        with self._cv:
            self._frames = frames
            self._buffers.append(frames)
            self._cv.notify_all()

    def before(self, stop):
        with self._cv:
            while not self._drain and stop > self._demand + self._ahead:
                self._check()
                self._cv.wait(.1)
            self._check()

    def advance(self, stop):
        with self._cv:
            self._ready = stop
            self._peak_ahead = max(self._peak_ahead, max(0, stop - self._demand))
            self._cv.notify_all()

    def allow_complete(self):
        """Break resource wait cycles when another process owns the prediction task."""
        with self._cv:
            self._drain = True
            self._cv.notify_all()

    def wait_complete(self):
        self.allow_complete()
        with self._cv:
            while not self._done:
                self._check()
                self._cv.wait(.1)
            self._check()
        return self._frames

    def __getitem__(self, index):
        frames_index = index[0] if isinstance(index, tuple) else index
        if isinstance(frames_index, slice):
            start, stop, step = frames_index.indices(len(self))
            wanted = max(start, stop - 1) + 1 if len(range(start, stop, step)) else 0
        elif isinstance(frames_index, (int, np.integer)):
            selected = int(frames_index)
            selected = selected + len(self) if selected < 0 else selected
            if not 0 <= selected < len(self):
                raise IndexError(selected)
            wanted = selected + 1
        else:
            values = np.asarray(frames_index)
            wanted = int(np.max(values % len(self))) + 1 if values.size else 0
        started = time.perf_counter()
        with self._cv:
            self._demand = max(self._demand, wanted)
            self._cv.notify_all()
            while self._frames is None or self._ready < wanted:
                self._check()
                self._cv.wait(.1)
            self._check()
            if self._first_read is None:
                self._first_read = time.perf_counter()
                self._read_before_complete = not self._done
            self._wait_seconds += time.perf_counter() - started
            return self._frames[index]

    def __array__(self, dtype=None, copy=None):
        values = np.asarray(self.wait_complete(), dtype=dtype)
        return values.copy() if copy else values

    @property
    def metadata(self):
        with self._cv:
            return {'enabled': True, 'ready_frames': self._ready, 'total_frames': len(self),
                'decode_seconds': (self._finished or time.perf_counter()) - self._started,
                'read_wait_worker_seconds': self._wait_seconds, 'read_before_decode_complete': self._read_before_complete,
                'prefetch_frames': self._ahead, 'peak_ahead_frames': self._peak_ahead, 'complete': self._done}

    def close(self):
        if not self._done:
            self._cancel.set()
        with self._cv:
            self._cv.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=30)
        # NumPy views retain their mappings; never unmap buffers while a reader holds a view.
        if not self._thread.is_alive():
            self._buffers.clear()
            if not self._done:
                self._frames = None

