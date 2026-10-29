"""Bounded RGB decoding with reusable, atomic disk-backed caches for long videos."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import mmap
import os
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .base import InferenceCancelled
from .cache_budget import CacheBudget, pin_mapping


def frame_cache_directory():
    return Path(os.environ.get('MINT_FRAME_CACHE_DIR') or Path(__file__).resolve().parents[2] / '.cache/video-frames')


def release_mapped_pages(frames):
    """Let the OS reclaim completed read-only mappings without dropping file data."""
    mapping = getattr(frames, '_mmap', None)
    if mapping is not None and hasattr(mapping, 'madvise'):
        try:
            mapping.madvise(mmap.MADV_DONTNEED)
        except (OSError, ValueError):
            pass


def sample_indices(count, source_fps, target_fps=None, max_frames=None):
    """Sample the whole timeline; no implicit frame-count limit or rounded stride."""
    if count <= 0 or not math.isfinite(source_fps) or source_fps <= 0:
        raise ValueError('Video needs a positive frame count and frame rate')
    fps = source_fps if not target_fps or target_fps >= source_fps else float(target_fps)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('Target frame rate must be positive, or 0 to preserve the source')
    if fps == source_fps:
        indices = np.arange(count, dtype=np.int64)
    else:
        indices = np.floor(np.arange(int(math.ceil(count * fps / source_fps))) * source_fps / fps).astype(np.int64)
        indices = indices[indices < count]
    if max_frames is not None:
        if int(max_frames) <= 0:
            raise ValueError('max_frames must be positive when explicitly supplied')
        indices = indices[:int(max_frames)]
    return indices, fps


def select_view(frames, view):
    if view == 'full':
        return frames
    if view not in {'left', 'right'}:
        raise ValueError('video_view must be full, left or right')
    width = frames.shape[-2]
    if width % 2:
        raise ValueError('Left/right views require an even-width side-by-side video')
    midpoint = width // 2
    return frames[..., :midpoint, :] if view == 'left' else frames[..., midpoint:, :]


def _check(cancel_check, cancelled_error):
    if cancel_check is not None and cancel_check():
        raise cancelled_error('Video decoding cancelled')


@contextmanager
def _cache_lock(path, cancel_check, cancelled_error):
    with path.open('a') as stream:
        while True:
            _check(cancel_check, cancelled_error)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def read_frames(reader, indices, *, source_path=None, view='full', cache_dir=None,
                on_step=None, cancel_check=None, cancelled_error=InferenceCancelled,
                memory_mib=None, decode_mib=None):
    """Return an ndarray or read-only .npy memmap; temporaries are bounded by bytes."""
    indices = np.asarray(indices, dtype=np.int64)
    _check(cancel_check, cancelled_error)
    if not len(indices):
        return np.empty((0, 0, 0, 3), dtype=np.uint8)
    first = select_view(reader.get_batch([int(indices[0])]).asnumpy(), view)
    if first.dtype != np.uint8 or first.ndim != 4 or first.shape[-1] != 3:
        raise ValueError('Decoder must return uint8 RGB frames')
    shape = (len(indices), *first.shape[1:])
    frame_bytes = int(np.prod(shape[1:]))
    # Decode budget uses native width: a cropped view still first decodes both eyes.
    native_frame_bytes = frame_bytes * (2 if view != 'full' else 1)
    memory_mib = int(os.environ.get('MINT_FRAME_MEMORY_MIB', '256')) if memory_mib is None else memory_mib
    decode_mib = int(os.environ.get('MINT_DECODE_MEMORY_MIB', '128')) if decode_mib is None else decode_mib
    if memory_mib <= 0 or decode_mib <= 0:
        raise ValueError('Frame cache and decoder memory budgets must be positive')
    chunk = max(1, min(96, int(decode_mib * 2**20) // native_frame_bytes))

    def progress(done):
        if on_step is not None:
            on_step(done, len(indices))

    def fill(output):
        for start in range(0, len(indices), chunk):
            _check(cancel_check, cancelled_error)
            stop = min(len(indices), start + chunk)
            decoded = reader.get_batch(indices[start:stop].tolist()).asnumpy()
            _check(cancel_check, cancelled_error)
            output[start:stop] = select_view(decoded, view)
            del decoded
            progress(stop)
            if isinstance(output, np.memmap) and ((start // chunk) % max(1, (512 * 2**20 // max(1, chunk * frame_bytes))) == 0):
                output.flush()
                release_mapped_pages(output)
        _check(cancel_check, cancelled_error)

    if len(indices) * frame_bytes <= memory_mib * 2**20:
        output = np.empty(shape, dtype=np.uint8)
        fill(output)
        return output

    directory = Path(cache_dir or frame_cache_directory())
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if source_path is None:
        raise ValueError('Disk-backed decoding requires the source path for cache identity')
    source = Path(source_path).resolve()
    stat = source.stat()
    from mint.runtime import file_identity
    source_identity = file_identity(source)
    identity = {'version': 2, 'source': str(source), 'size': stat.st_size,
                'ctime_ns': stat.st_ctime_ns, 'inode': stat.st_ino,
                'mtime_ns': stat.st_mtime_ns, 'shape': list(shape), 'view': view,
                'indices_sha256': hashlib.sha256(indices.tobytes()).hexdigest()}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    destination = directory / (key + '.npy')
    manifest = directory / (key + '.json')
    with _cache_lock(directory / (key + '.lock'), cancel_check, cancelled_error):
        try:
            if json.loads(manifest.read_text()) == identity:
                cached = np.load(destination, mmap_mode='r', allow_pickle=False)
                if cached.shape == shape and cached.dtype == np.uint8:
                    pin_mapping(cached, destination)
                    progress(len(indices))
                    return cached
                cached._mmap.close()
        except (FileNotFoundError, ValueError, OSError):
            pass
        # Only this key's writer can hold the lock; these belong to interrupted writers.
        for stale in directory.glob(key + '.partial-*'):
            stale.unlink(missing_ok=True)
        budget = CacheBudget(directory)
        temporary = directory / (key + f'.partial-{os.getpid()}-{threading.get_ident()}.npy')
        metadata_temp = temporary.with_suffix('.json')
        mapped = None
        try:
            with budget.reserve(destination, len(indices) * frame_bytes + 4096):
                mapped = np.lib.format.open_memmap(temporary, mode='w+', dtype=np.uint8, shape=shape)
                temporary.chmod(0o600)
                fill(mapped)
                if file_identity(source) != source_identity:
                    raise RuntimeError('Video changed during decoding; retry after the recording is complete')
                mapped.flush()
                mapped._mmap.close()
                mapped = None
                metadata_temp.write_text(json.dumps(identity, sort_keys=True))
                os.replace(temporary, destination)
                os.replace(metadata_temp, manifest)
                return pin_mapping(np.load(destination, mmap_mode='r', allow_pickle=False), destination)

        finally:
            if mapped is not None:
                mapped._mmap.close()
            temporary.unlink(missing_ok=True)
            metadata_temp.unlink(missing_ok=True)
