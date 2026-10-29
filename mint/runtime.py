"""Private atomic metadata, input identities and stage timing shared by entry points."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def file_identity(value) -> dict:
    path = Path(value).expanduser().resolve()
    try:
        stat = path.stat()
        return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                'ctime_ns': stat.st_ctime_ns, 'inode': stat.st_ino}
    except FileNotFoundError:
        return {'path': str(path), 'missing': True}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, default=str)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_npz(path, **arrays):
    import numpy as np
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            np.savez_compressed(stream, **arrays)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def video_identity(path, indices, view, fps):
    import numpy as np
    return {'source': file_identity(path), 'view': view, 'fps': float(fps),
            'indices': hashlib.sha256(np.asarray(indices, dtype=np.int64).tobytes()).hexdigest()}


def model_identity(checkpoint, config):
    """Fingerprint the exact weight file and parsed configuration a loader will use."""
    import yaml
    path = Path(checkpoint).expanduser().resolve()
    if path.is_dir():
        for pattern in ('model.safetensors', 'pytorch_model.bin', '*.safetensors', 'pytorch_model*.bin', '*.pt', '*.pth', '*.ckpt'):
            hits = sorted(p for p in path.glob(pattern) if p.is_file())
            if hits:
                path = hits[0]
                break
    configuration = config if isinstance(config, dict) else yaml.safe_load(Path(config).read_text())
    return digest({'weights': file_identity(path), 'config': configuration})


class StageTimer:
    def __init__(self):
        self.started = time.perf_counter()
        self.seconds = {}

    @contextmanager
    def stage(self, name):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[name] = self.seconds.get(name, 0.0) + time.perf_counter() - started

    def snapshot(self):
        return {**self.seconds, 'end_to_end_seconds': time.perf_counter() - self.started}
