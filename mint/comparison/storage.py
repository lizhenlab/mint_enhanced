"""Admission quota for durable results and resumable native work, without eviction."""
from __future__ import annotations

import os
from pathlib import Path
from mint.inference.cache_budget import CacheBudget

OWNED_ROOT = Path(__file__).resolve().parents[2]


class DurableRunBudget(CacheBudget):
    def __init__(self, root):
        super().__init__(root, pattern='**/*', max_gib=float(os.environ.get('MINT_COMPARISON_RUN_MAX_GIB', '256')))

    def _files(self):
        if self.pattern != '**/*':
            return super()._files()
        # Adopted immutable stores use directory symlinks to preserve file
        # identities. Count their physical bytes without scanning external data
        # or visiting a linked directory/file more than once.
        files, directories, identities = [], set(), set()
        for parent, children, names in os.walk(self.directory, followlinks=True):
            folder = Path(parent)
            try:
                stat = folder.stat()
            except FileNotFoundError:
                children[:] = []; continue
            identity = (stat.st_dev, stat.st_ino)
            if identity in directories:
                children[:] = []; continue
            directories.add(identity)
            children[:] = [name for name in children if not (folder / name).is_symlink() or
                           (folder / name).resolve().is_relative_to(OWNED_ROOT)]
            for name in names:
                path = folder / name
                if name.startswith('.') or '.partial-' in name or path.is_symlink() and not path.resolve().is_relative_to(OWNED_ROOT):
                    continue
                try:
                    stat = path.stat(); key = (stat.st_dev, stat.st_ino)
                    if path.is_file() and key not in identities:
                        files.append(path); identities.add(key)
                except FileNotFoundError:
                    continue
        return files

    def _evict(self, needed, reserved, exclude=None):
        # A completed numerical result or a native recovery stage is never removed by LRU.
        total = 0
        for path in self._files():
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                pass
        return total, []


class PreviewBudget(DurableRunBudget):
    def __init__(self, root):
        CacheBudget.__init__(self, root, pattern='*/input.mp4',
            max_gib=float(os.environ.get('MINT_COMPARISON_PROXY_MAX_GIB', '16')))
        # RGB and proxy files share the directory, but have independent capacities.
        self.state = self.directory / '.proxy-reservations'


def estimate_native_bytes(request, sample):
    frames = sample['frames']
    estimate = frames * 40000 + 128 * 2**20
    if request['algorithm'].startswith('hawor') and not request.get('import'):
        cap = request['options']['analysis_width'] or sample['width']
        width = min(cap, sample['width'])
        height = max(2, round(sample['height'] * width / sample['width']))
        estimate += frames * width * height * 12
    return estimate
