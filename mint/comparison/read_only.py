"""Keep external source trees read-only inside native Python workers."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def protect_native_roots(roots):
    protected = tuple(Path(root).resolve() for root in roots)
    def resolve(path, dir_fd=None):
        if isinstance(path, int) or path is None:
            return None
        path = Path(os.fsdecode(path))
        if not path.is_absolute() and dir_fd not in (None, -1):
            path = Path(f'/proc/self/fd/{dir_fd}').resolve() / path
        return path.resolve()
    def check(path, dir_fd=None):
        path = resolve(path, dir_fd)
        if path is None:
            return
        if any(path == root or root in path.parents for root in protected):
            raise PermissionError(f'External native tree is read-only; write outputs inside MINT: {path}')
    def audit(event, args):
        if event == 'open':
            path, mode, flags = args
            if any(value in (mode or '') for value in 'wax+') or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                check(path)
        elif event=='os.mkdir' and resolve(args[0], args[2]).is_dir():
            return
        elif event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.truncate', 'os.utime'}:
            fd = args[-1] if event in {'os.remove','os.rmdir','os.mkdir','os.chmod','os.utime'} else None
            check(args[0], fd)
        elif event in {'os.rename', 'os.link'}:
            check(args[0], args[2]); check(args[1], args[3])
        elif event=='os.symlink':
            check(args[1], args[2])
    sys.addaudithook(audit)
    # CPython's open audit event omits dir_fd; validate that variant before dispatch.
    original_open=os.open
    def guarded_open(path,flags,mode=0o777,*,dir_fd=None):
        if flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
            check(path,dir_fd)
        return original_open(path,flags,mode,dir_fd=dir_fd)
    os.open=guarded_open


def configure_native_cache(directory):
    directory = Path(directory)
    for name, part in {'TMPDIR': 'tmp', 'XDG_CACHE_HOME': 'cache', 'TORCH_HOME': 'torch',
                       'TORCH_EXTENSIONS_DIR': 'extensions', 'YOLO_CONFIG_DIR': 'ultralytics',
                       'TRITON_CACHE_DIR':'triton','TORCHINDUCTOR_CACHE_DIR':'inductor',
                       'MPLCONFIGDIR':'matplotlib'}.items():
        target = directory / part; target.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(target)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
