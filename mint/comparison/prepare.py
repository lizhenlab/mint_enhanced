"""Prepare one immutable, shared frame timeline without algorithm-specific sampling."""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, file_identity
from mint.inference.cache_budget import CacheBudget, file_lock
from mint.inference.frame_store import read_frames, sample_indices, select_view
from mint.visualization.encoding import atomic_video_file


@contextmanager
def preparation_lock(path, cancel, progress):
    from mint.inference.base import InferenceCancelled
    while True:
        stack = ExitStack()
        try:
            stack.enter_context(file_lock(path, blocking=False))
        except BlockingIOError:
            stack.close()
            if cancel():
                raise InferenceCancelled('Cancelled while waiting for shared sample preparation')
            progress(stage='waiting_sample', detail='Another selected algorithm is preparing this exact input')
            time.sleep(.1)
        else:
            break
    with stack:
        if cancel():
            raise InferenceCancelled('Sample preparation cancelled')
        yield


def prepare_sample(request, directory, progress=lambda **kw: None, cancel=lambda: False):
    from decord import VideoReader
    from mint.inference.base import InferenceCancelled
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = request['source']['path']
    if file_identity(source) != request['source']:
        raise ValueError('Input video changed; submit a new sample')
    with preparation_lock(directory / '.prepare.lock', cancel, progress):
        manifest = directory / 'sample.json'
        if manifest.exists():
            record = json.loads(manifest.read_text())
            if (record['request'] == request and file_identity(record['frames_path']) == record.get('frames_identity')
                    and file_identity(record['video_path']) == record.get('video_identity')):
                return record
        reader = VideoReader(source, num_threads=4)
        source_fps = float(reader.get_avg_fps())
        indices, fps = sample_indices(len(reader), source_fps, request['target_fps'])
        indices = indices[indices >= request['start_frame']]
        if request['max_frames'] is not None:
            indices = indices[:request['max_frames']]
        if not len(indices):
            raise ValueError('The requested time range is empty')
        timestamps = np.asarray(reader.get_frame_timestamp(indices.tolist()), dtype=np.float64)[:, 0]
        if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) <= 0):
            raise ValueError('Decoder did not provide a strictly increasing source PTS timeline')
        probe = reader.get_batch([int(indices[0])]).asnumpy()
        source_h, source_w = probe.shape[1:3]
        probe = select_view(probe, request['view'])
        height, width = probe.shape[1:3]
        shape = (len(indices), height, width, 3)
        frames_path = directory / 'frames.npy'
        budget = CacheBudget(directory.parent, pattern='*/frames.npy', pin_parent=True,
                             max_gib=float(os.environ.get('MINT_COMPARISON_INPUT_MAX_GIB', '256')))
        with file_lock(directory / '.active.pin', shared=True), budget.reserve(frames_path, int(np.prod(shape)) + 4096):
            temporary = directory / f'frames.partial-{os.getpid()}.npy'
            frames = np.lib.format.open_memmap(temporary, mode='w+', dtype=np.uint8, shape=shape)
            try:
                chunk = max(1, min(96, 64 * 2**20 // (source_h * source_w * 3)))
                for start in range(0, len(indices), chunk):
                    if cancel():
                        raise InferenceCancelled('Sample preparation cancelled')
                    stop = min(len(indices), start + chunk)
                    frames[start:stop] = select_view(reader.get_batch(indices[start:stop].tolist()).asnumpy(), request['view'])
                    progress(stage='decode', done=stop, total=len(indices))
                    if start % (chunk * 8) == 0:
                        frames.flush()
                        from mint.inference.frame_store import release_mapped_pages
                        release_mapped_pages(frames)
                frames.flush()
                if file_identity(source) != request['source']:
                    raise ValueError('Source changed during preparation')
                os.replace(temporary, frames_path)
                from mint.visualization.draw import H264PipeWriter
                from .storage import PreviewBudget
                preview_budget = PreviewBudget(directory.parent)
                def render(destination):
                    writer = H264PipeWriter(destination, fps, (width + width % 2, height + height % 2))
                    try:
                        for index, frame in enumerate(frames):
                            if cancel():
                                raise InferenceCancelled('Sample preparation cancelled')
                            if height % 2 or width % 2:
                                frame = np.pad(frame, ((0, height % 2), (0, width % 2), (0, 0)), mode='edge')
                            writer.write(frame[:, :, ::-1])
                            if index % 96 == 0:
                                progress(stage='preview', done=index, total=len(indices))
                                import shutil
                                if shutil.disk_usage(directory).free < preview_budget.minimum_free:
                                    raise OSError('Preview encoding reached the disk free-space reserve')
                    finally:
                        writer.close()
                    used = sum(path.stat().st_size for path in preview_budget._files() if path != directory / 'input.mp4')
                    if used + destination.stat().st_size > preview_budget.maximum:
                        raise OSError('Comparison preview quota exceeded; archive selected inputs or increase MINT_COMPARISON_PROXY_MAX_GIB')
                with preparation_lock(directory.parent / '.proxy-encode.lock', cancel, progress):
                    with preview_budget.reserve(directory / 'input.mp4', max(2**20, int(np.prod(shape)) // 32)):
                        proxy = atomic_video_file(directory / 'input.mp4', render, len(indices))
                camera = request['camera']
                if camera:
                    K = np.asarray(camera['K'], np.float64).copy()
                    K[0] *= source_w / camera['width']
                    K[1] *= source_h / camera['height']
                    if request['view'] == 'right':
                        K[0, 2] -= source_w // 2
                    camera = {'K': K.tolist(), 'source': camera['source'], 'model': 'pinhole', 'grid': [width, height]}
                record = {'schema': 1, 'id': request['id'], 'request': request, 'source': request['source'],
                    'source_frames': len(reader), 'source_fps': source_fps, 'frames': len(indices), 'fps': fps,
                    'source_frame_indices': indices.tolist(), 'timestamps_s': timestamps.tolist(),
                    'time_domain': 'source_video_pts_seconds', 'width': width, 'height': height,
                    'frames_path': str(frames_path), 'video_path': str(proxy), 'camera': camera,
                    'frames_identity': file_identity(frames_path), 'video_identity': file_identity(proxy),
                    'proxy_pixel_grid': [width + width % 2, height + height % 2],
                    'input_policy': 'Identical decoded RGB frames for all workers; native resize and camera remapping are declared by each algorithm'}
                atomic_json(manifest, record)
                return record
            finally:
                frames._mmap.close()
                temporary.unlink(missing_ok=True)
