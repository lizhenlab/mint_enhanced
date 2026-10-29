"""Atomic raw-window checkpoints and durable task metadata for restart recovery."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, digest
from .cache_budget import CacheBudget, file_lock

TASK_SCHEMA = 1


def task_root():
    return Path(os.environ.get('MINT_WINDOW_CACHE_DIR') or Path(__file__).resolve().parents[2] / '.cache/window-predictions')


class WindowCheckpoint:
    def __init__(self, identity, bounds, parameters=None, directory=None):
        self.identity = {**identity, 'schema': TASK_SCHEMA, 'bounds': bounds}
        self.id = digest(self.identity)
        self.directory = Path(directory or task_root())
        self.folder = self.directory / self.id
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.bounds = bounds
        self.manifest = self.folder / 'task.json'
        self.lock = threading.RLock()
        self.reused = self.written = 0
        self.completed = set()
        self.budget = CacheBudget(self.directory, pattern='*/*.npz', pin_parent=True,
            max_gib=float(os.environ.get('MINT_WINDOW_CACHE_MAX_GIB', '16')))
        self.cache_warning = None
        self._pin = (self.folder / '.active.pin').open('a')
        import fcntl
        fcntl.flock(self._pin, fcntl.LOCK_SH)
        self.update(status='running', stage='inference', identity=self.identity,
                    parameters=parameters or {}, total_windows=len(bounds), frames=max(end for _, end in bounds),
                    pid=os.getpid(), pid_token=__import__('mint.inference.cache_budget', fromlist=['_token'])._token(os.getpid()), started_at=time.time())

    def update(self, **values):
        with self.lock, file_lock(self.folder / '.manifest.lock'):
            try:
                state = json.loads(self.manifest.read_text())
            except (OSError, ValueError):
                state = {'id': self.id, 'created_at': time.time()}
            state.update(values, updated_at=time.time())
            atomic_json(self.manifest, state)

    def _path(self, index):
        return self.folder / f'{index:08d}.npz'

    def load(self, index):
        try:
            with np.load(self._path(index), allow_pickle=False) as values:
                result = {key: values[key] for key in values.files}
            length = self.bounds[index][1] - self.bounds[index][0]
            if result['pose_enc'].shape != (1, length, 9) or any(
                    v.shape[:2] != (1, length) or not np.isfinite(v).all() for v in result.values()):
                return None
            hand, presence = self.identity.get('output_heads', (False, False))
            if hand and result.get('hand', np.empty(0)).shape != (1, length, 218):
                return None
            if presence and result.get('hand_presence_logits', np.empty(0)).shape != (1, length, 2):
                return None
            with self.lock:
                self.completed.add(index)
                self.reused += 1
            return result
        except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
            return None

    def save(self, index, result):
        path = self._path(index)
        arrays = {key: np.asarray(value) for key, value in result.items()}
        if any(not np.isfinite(value).all() for value in arrays.values()):
            raise ValueError('Non-finite raw prediction cannot be checkpointed')
        try:
            with self.budget.reserve(path, sum(v.nbytes for v in arrays.values()) + 8192):
                fd, temporary = tempfile.mkstemp(prefix='.' + path.name, suffix='.npz', dir=self.folder)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        np.savez_compressed(stream, **arrays)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
        except OSError as exc:
            # The result itself is still valid. A full checkpoint cache never truncates inference.
            with self.lock:
                if self.cache_warning is None:
                    print(f'[checkpoint] {exc}; inference continues without saving additional windows', flush=True)
                self.cache_warning = str(exc)
                self.completed.add(index)
            return
        with self.lock:
            self.completed.add(index)
            self.written += 1
            self.update(completed_windows=len(self.completed), reused_windows=self.reused,
                        new_windows=self.written)

    def finish(self, status, **values):
        self.update(status=status, completed_windows=len(self.completed), reused_windows=self.reused,
                    new_windows=self.written, cache_warning=self.cache_warning, **values)

    def close(self):
        if self._pin is not None:
            self._pin.close()
            self._pin = None

    @property
    def metadata(self):
        return {'id': self.id, 'reused_windows': self.reused, 'new_windows': self.written,
                'total_windows': len(self.bounds), 'manifest': str(self.manifest), 'cache_warning': self.cache_warning}


def list_tasks(directory=None, limit=100):
    root = Path(directory or task_root())
    tasks = []
    for path in root.glob('*/task.json'):
        try:
            item = json.loads(path.read_text())
            if item.get('status') == 'running':
                from .cache_budget import _token
                if _token(item.get('pid', 0)) != item.get('pid_token'):
                    item['status'] = 'interrupted'
            tasks.append(item)
        except (ValueError, OSError):
            continue
    return sorted(tasks, key=lambda v: v.get('updated_at', 0), reverse=True)[:limit]


def update_task(task_id, **values):
    if not task_id or len(task_id) != 64 or any(c not in '0123456789abcdef' for c in task_id):
        return
    path = task_root() / task_id / 'task.json'
    with file_lock(path.parent / '.manifest.lock'):
        try:
            item = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        item.update(values, updated_at=time.time())
        atomic_json(path, item)


def prune_tasks(directory=None):
    """Bound checkpoint storage; active inference sessions always keep their directory."""
    import shutil
    root = Path(directory or task_root())
    maximum = int(float(os.environ.get('MINT_WINDOW_CACHE_MAX_GIB', '16')) * 2**30)
    if maximum <= 0:
        raise ValueError('MINT_WINDOW_CACHE_MAX_GIB must be positive')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file_lock(root / '.budget.lock'):
        folders = [p for p in root.iterdir() if p.is_dir()]
        sizes = {p: sum(x.stat().st_size for x in p.glob('*.npz')) for p in folders}
        used = sum(sizes.values())
        for folder in sorted(folders, key=lambda p: (p / 'task.json').stat().st_mtime if (p / 'task.json').exists() else 0):
            if used <= maximum:
                break
            try:
                with file_lock(folder / '.active.pin', blocking=False):
                    # Keep task metadata for history; only raw prediction blobs are evicted.
                    for path in folder.glob('*.npz'):
                        path.unlink(missing_ok=True)
                    used -= sizes[folder]
            except BlockingIOError:
                continue
        return {'used_bytes': used, 'limit_bytes': maximum, 'tasks': len(folders)}
