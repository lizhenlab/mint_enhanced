"""Cross-process cache quotas, reservations, LRU eviction and live-reader pins."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import threading
import time
import uuid
import weakref
from contextlib import contextmanager, ExitStack
from pathlib import Path

from mint.runtime import atomic_json


def _token(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except OSError:
        try:
            os.kill(pid, 0)
            return str(pid)
        except OSError:
            return None


@contextmanager
def file_lock(path, *, shared=False, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = path.open('a')
    try:
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(stream, operation | (0 if blocking else fcntl.LOCK_NB))
        yield stream
    finally:
        stream.close()


def pin_mapping(mapping, destination):
    """Hold a shared file lease for as long as the ndarray and all its views exist."""
    stream = Path(destination).with_suffix('.pin').open('a')
    try:
        fcntl.flock(stream, fcntl.LOCK_SH)
        os.utime(stream.name, None)
    except Exception:
        stream.close()
        raise
    weakref.finalize(mapping, stream.close)
    return mapping


class CacheBudget:
    def __init__(self, directory, *, max_gib=None, min_free_gib=None, pattern='*.npy', pin_parent=False):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.maximum = int(float(os.environ.get('MINT_FRAME_CACHE_MAX_GIB', '256')) * 2**30) if max_gib is None else int(max_gib * 2**30)
        self.minimum_free = int(float(os.environ.get('MINT_CACHE_MIN_FREE_GIB', '16')) * 2**30) if min_free_gib is None else int(min_free_gib * 2**30)
        if self.maximum <= 0 or self.minimum_free < 0:
            raise ValueError('Cache capacity must be positive and free-space reserve nonnegative')
        self.pattern = pattern
        self.pin_parent = pin_parent
        self.state = self.directory / '.reservations'

    def _reservations(self):
        try:
            values = json.loads(self.state.read_text())
        except (OSError, ValueError):
            values = {}
        return {k: v for k, v in values.items() if _token(v['pid']) == v['token']}

    def _files(self):
        return [p for p in self.directory.glob(self.pattern)
                if p.is_file() and '.partial-' not in p.name and not p.name.startswith('.')]

    def _evict(self, needed, reserved, exclude=None):
        files = self._files()
        used = sum(p.stat().st_size for p in files)
        def last_used(path):
            pin = path.with_suffix('.pin')
            return pin.stat().st_mtime if pin.exists() else path.stat().st_mtime
        removed = []
        for path in sorted(files, key=last_used):
            free = shutil.disk_usage(self.directory).free
            if used + reserved + needed <= self.maximum and free - reserved - needed >= self.minimum_free:
                break
            if path == exclude:
                continue
            # A writer acquires the key lock before reserving space. Never block here.
            try:
                with file_lock(path.with_suffix('.lock'), blocking=False):
                    with file_lock(path.with_suffix('.pin'), blocking=False):
                        with ExitStack() as stack:
                            if self.pin_parent:
                                stack.enter_context(file_lock(path.parent / '.active.pin', blocking=False))
                            size = path.stat().st_size
                            path.unlink()
                            path.with_suffix('.json').unlink(missing_ok=True)
                            used -= size
                            removed.append(str(path))
            except (BlockingIOError, FileNotFoundError):
                continue
        return used, removed

    @contextmanager
    def reserve(self, destination, size):
        size = int(size)
        if size > self.maximum:
            raise OSError(f'Video cache needs {size/2**30:.1f} GiB; configured cache capacity is '
                          f'{self.maximum/2**30:.1f} GiB. Increase MINT_FRAME_CACHE_MAX_GIB or select another cache disk.')
        key = f'{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}'
        with file_lock(self.directory / '.budget.lock'):
            reservations = self._reservations()
            reserved = sum(value['bytes'] for value in reservations.values())
            used, _ = self._evict(size, reserved, Path(destination))
            free = shutil.disk_usage(self.directory).free
            if used + reserved + size > self.maximum or free - reserved - size < self.minimum_free:
                raise OSError('Insufficient frame-cache disk space or cache budget; active files are pinned. '
                              'Increase MINT_FRAME_CACHE_MAX_GIB or set MINT_FRAME_CACHE_DIR to another disk.')
            reservations[key] = {'pid': os.getpid(), 'token': _token(os.getpid()), 'bytes': size, 'created_at': time.time()}
            atomic_json(self.state, reservations)
        try:
            yield
        finally:
            with file_lock(self.directory / '.budget.lock'):
                reservations = self._reservations()
                reservations.pop(key, None)
                atomic_json(self.state, reservations)

    def status(self):
        files = self._files()
        return {'directory': str(self.directory), 'files': len(files),
                'used_bytes': sum(p.stat().st_size for p in files), 'limit_bytes': self.maximum,
                'disk_free_bytes': shutil.disk_usage(self.directory).free,
                'reserved_bytes': sum(v['bytes'] for v in self._reservations().values())}
