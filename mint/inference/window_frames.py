"""Prepare only the input window being consumed by a GPU worker."""

from __future__ import annotations

import os
import threading
import time

import numpy as np

from .frame_store import release_mapped_pages


class WindowFrames:
    def __init__(self, frames, size_hw, cancel_check=None, cancelled_error=RuntimeError):
        if frames.ndim != 4 or frames.shape[-1] != 3:
            raise ValueError('Expected RGB input [N,H,W,3]')
        self.frames, self.size_hw = frames, tuple(size_hw)
        self.shape = (len(frames), 3, *self.size_hw)
        self.cancel_check, self.cancelled_error = cancel_check, cancelled_error
        self._lock = threading.Lock()
        self._seconds, self._peak_chunk = 0.0, 0

    def __getitem__(self, index):
        started = time.perf_counter()
        selected = self.frames[index]
        if selected.ndim == 3:
            return self._prepare(selected[None])[0]
        return self._prepare(selected, started)

    def _prepare(self, frames, started=None):
        import torch
        from data.transforms import preprocess_frames
        started = time.perf_counter() if started is None else started
        # Native uint8 copy + float conversion + normalization temporary.
        per_frame = max(1, int(np.prod(frames.shape[1:])) * 9)
        budget = int(os.environ.get('MINT_PREPROCESS_MEMORY_MIB', '256'))
        if budget <= 0:
            raise ValueError('MINT_PREPROCESS_MEMORY_MIB must be positive')
        chunk = max(1, min(32, budget * 2**20 // per_frame))
        output = torch.empty((len(frames), 3, *self.size_hw), dtype=torch.float32)
        for start in range(0, len(frames), chunk):
            if self.cancel_check is not None and self.cancel_check():
                raise self.cancelled_error('Cancelled during window preprocessing')
            # Copy only this small chunk: read-only memmaps must not become writable torch views.
            values = np.array(frames[start:start + chunk], copy=True)
            part = preprocess_frames(values, self.size_hw)
            output[start:start + len(part)].copy_(part)
            del part, values
        release_mapped_pages(self.frames)
        with self._lock:
            self._seconds += time.perf_counter() - started
            self._peak_chunk = max(self._peak_chunk, min(chunk, len(frames)))
        return output

    def unsqueeze(self, dim):
        if dim != 0:
            raise ValueError('Only a batch dimension can be added to windowed frames')
        source = self
        class Batch:
            shape = (1, *source.shape)
            def __getitem__(self, index):
                batch_index, frame_index = index
                return source[frame_index].unsqueeze(0)[batch_index]
        return Batch()

    @property
    def metadata(self):
        with self._lock:
            return {'preprocess_strategy': 'per_window',
                    'preprocess_worker_seconds': self._seconds,
                    'preprocess_peak_native_frames': self._peak_chunk}
