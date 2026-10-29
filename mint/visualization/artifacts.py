"""Versioned, verified video artifacts shared across Viewer processes and restarts."""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import shutil
import tempfile
from pathlib import Path

from mint.runtime import atomic_json, digest, file_identity
from mint.inference.cache_budget import CacheBudget, file_lock
from .encoding import validate_video


@functools.lru_cache(maxsize=1)
def render_version():
    root = Path(__file__).resolve().parents[2]
    files = [p for folder in ('mint', 'eval/model_effect/visualization', 'model_train/models',
                              'model_train/data', 'model_train/utils')
             for p in (root / folder).rglob('*.py')]
    return digest({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)})


def artifact_identity(path):
    # Creating/removing another hardlink changes ctime without changing the video.
    return {k: v for k, v in file_identity(path).items() if k != 'ctime_ns'}


class VideoArtifacts:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.environ.get('MINT_RENDER_CACHE_DIR') or
                              Path(__file__).resolve().parents[2] / '.cache/rendered-results')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.budget = CacheBudget(self.directory, pattern='*.mp4',
            max_gib=float(os.environ.get('MINT_RENDER_CACHE_MAX_GIB', '64')))

    def lookup(self, result_id):
        if len(result_id) != 64 or any(c not in '0123456789abcdef' for c in result_id):
            return None
        path = self.directory / (result_id + '.mp4')
        try:
            metadata = json.loads(path.with_suffix('.json').read_text())
            if metadata['id'] != result_id or metadata['file'] != artifact_identity(path):
                return None
            path.with_suffix('.pin').touch(exist_ok=True)
            return path
        except (OSError, ValueError, KeyError):
            return None

    def resolve(self, identity, produce, *, expected_frames):
        result_id = digest({'schema': 1, 'identity': identity})
        destination = self.directory / (result_id + '.mp4')
        with file_lock(destination.with_suffix('.lock')):
            cached = self.lookup(result_id)
            if cached is not None:
                pin = destination.with_suffix('.pin')
                pin.touch(exist_ok=True)
                return cached
            source = Path(produce())
            expected_frames = expected_frames() if callable(expected_frames) else expected_frames
            probe = validate_video(source, expected_frames)
            with self.budget.reserve(destination, source.stat().st_size):
                fd, name = tempfile.mkstemp(prefix='.' + result_id, suffix='.mp4', dir=self.directory)
                os.close(fd)
                temporary = Path(name)
                try:
                    temporary.unlink()
                    try:
                        os.link(source, temporary)
                    except OSError:
                        shutil.copyfile(source, temporary)
                    os.replace(temporary, destination)
                    atomic_json(destination.with_suffix('.json'), {'id': result_id, 'identity': identity,
                        'frames': expected_frames, 'video': probe, 'file': artifact_identity(destination)})
                finally:
                    temporary.unlink(missing_ok=True)
            return destination


def persistent_video(method):
    signature = inspect.signature(method)
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        params = {k: v for k, v in bound.arguments.items() if k not in {'self', 'eid', 'on_step'}}
        eid = bound.arguments['eid']
        from mint.inference.hand_smoothing import canonical_hand_mode
        if 'hand_mode' in params:
            params['hand_mode'] = canonical_hand_mode(params['hand_mode'])
        context = self.media_context(eid)
        gt_only = method.__name__ == 'mp4_gt' or params.get('raw') or params.get('source') == 'gt'
        identity = {**context, 'model_id': 'gt' if gt_only else context['model_id'],
                    'kind': method.__name__, 'parameters': params}
        if gt_only:
            identity['inference_id'] = 'gt'
        if method.__name__ in {'mp4', 'mp4_pred'} and self.is_no_truth(eid):
            identity['kind'] = 'prediction2d'
            identity['parameters'] = {k: params[k] for k in ('mode', 'cam_mode', 'hand_mode',
                                                           'pred_betas_mean', 'pred_fov_mean')}
        # Check before decoding: a verified export can be served without loading RGB or rerendering.
        result_id = digest({'schema': 1, 'identity': identity})
        cached = self._artifacts.lookup(result_id)
        if cached is not None:
            return cached
        def produce():
            path = method(self, *args, **kwargs)
            try:
                validate_video(path, int(self.raw(eid)['frames'].shape[0]))
            except RuntimeError:
                # An indexed video may share an inode with an older private render.
                # Regenerate that private cache too if its encoder output was damaged.
                if not Path(path).resolve().is_relative_to(Path(self.cache_dir).resolve()):
                    raise
                Path(path).unlink(missing_ok=True)
                for name in ('_mp4', '_world_mp4', '_mujoco_mp4', '_retarget_mp4'):
                    cache = getattr(self, name)
                    for key, value in list(cache.items()):
                        if value == path:
                            cache.pop(key, None)
                path = method(self, *args, **kwargs)
            self._check_model_generation()
            self._validate_input_version(eid)
            return path
        return self._artifacts.resolve(identity, produce,
            expected_frames=lambda: int(self.raw(eid)['frames'].shape[0]))
    return wrapped
