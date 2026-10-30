"""Render immutable standardized geometry; never invoke a hand-estimation network."""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, file_identity
from mint.inference.cache_budget import CacheBudget, file_lock, _token
from mint.visualization.encoding import atomic_video_file
from .contracts import load_result, transform_points


def render_inputs(request):
    metadata, arrays = load_result(request['result_dir'])
    sample = json.loads(Path(request['sample_manifest']).read_text())
    ids = np.asarray(sample['source_frame_indices'][request['start']:request['end']], np.int64)
    positions = np.searchsorted(arrays['source_frame_indices'], ids)
    matched = positions < len(arrays['source_frame_indices'])
    matched[matched] &= arrays['source_frame_indices'][positions[matched]] == ids[matched]
    if not matched.all():
        raise ValueError('工具所选片段含原结果缺失帧，请选择完整覆盖的片段')
    cameras = np.broadcast_to(np.eye(4), (len(ids), 4, 4)).copy()
    if request['space'] == 'world':
        cameras = np.asarray(arrays['T_world_camera'][positions]).copy()
        if not arrays['pose_valid'][positions].all():
            raise ValueError('World rendering requires valid camera geometry for each selected frame')
    kept = np.asarray(arrays['geometry_valid'][positions]).copy()
    if not request['include_absent'] and 'presence' in arrays:
        kept &= arrays['presence'][positions]
    if not kept.any():
        raise ValueError('The selected interval has no visible hand geometry')
    world = {}
    for slot, side in enumerate(('left', 'right')):
        points = np.asarray(arrays['joints_camera_m'][positions, slot]).copy()
        if request['space'] == 'world':
            points = transform_points(cameras, points)
        world[side] = {'joints': points}
        if 'vertices_camera_m' in arrays:
            vertices = np.asarray(arrays['vertices_camera_m'][positions, slot]).copy()
            world[side]['verts'] = transform_points(cameras, vertices) if request['space'] == 'world' else vertices
    # Finite hidden display scaffolding avoids NaNs in renderer bounding boxes. The
    # source arrays remain unchanged, and kept masks prevent these scaffolds appearing.
    for slot, side in enumerate(('left', 'right')):
        good = np.flatnonzero(kept[:, slot])
        other = 'right' if slot == 0 else 'left'
        for name, value in world[side].items():
            if len(good):
                nearest = good[np.minimum(np.searchsorted(good, np.arange(len(ids))), len(good) - 1)]
                value[~kept[:, slot]] = value[nearest[~kept[:, slot]]]
            else:
                candidate = world[other][name]
                valid = np.flatnonzero(np.isfinite(candidate).all(axis=(1, 2)))
                if not len(valid):
                    raise ValueError('No finite display scaffold is available for this hand topology')
                value[:] = candidate[valid[0]]
    return world, cameras, kept, np.asarray(arrays['K'][positions[0]]), sample, ids


def main(argv=None):
    parser = argparse.ArgumentParser();parser.add_argument('--request', required=True)
    path = Path(parser.parse_args(argv).request)
    request = json.loads(path.read_text());folder = path.parent
    def progress(**values):
        atomic_json(folder / 'status.json', {'id': request['id'], 'pid': os.getpid(), 'pid_token': _token(os.getpid()), 'updated': time.time(), **values})
    def cancelled():
        if (folder / 'cancel').exists():
            raise InterruptedError('Rendering cancelled')
    def terminate(*args):
        raise InterruptedError('Rendering interrupted')
    signal.signal(signal.SIGTERM, terminate)
    try:
        cancelled()
        if file_identity(Path(request['result_dir']) / 'result.json') != request['result']:
            raise ValueError('Source result changed since this render request')
        world, cameras, kept, K, sample, ids = render_inputs(request)
        budget = CacheBudget(folder.parent, pattern='*/video.mp4', pin_parent=True,
                             max_gib=float(os.environ.get('MINT_RENDER_MAX_GIB', '16')))
        output = folder / 'video.mp4'
        tick = time.perf_counter()
        def on_step(done, total):
            nonlocal tick
            cancelled()
            if os.environ.get('MINT_RENDER_SHARED_FOREIGN') == '1':
                deadline = time.monotonic() + max(0., time.perf_counter() - tick) * (1 / .3 - 1)
                while time.monotonic() < deadline:
                    cancelled();time.sleep(min(.1, max(0., deadline - time.monotonic())))
            tick = time.perf_counter()
            progress(status='running', progress={'stage': request['kind'], 'done': int(done), 'total': int(total)})
            if __import__('shutil').disk_usage(folder).free < budget.minimum_free or any(
                    path.stat().st_size > budget.maximum for path in folder.glob('*.mp4')):
                raise OSError('Rendering reached disk-space reserve')
        from eval.model_effect.visualization.render.mujoco_video import render_world_video
        from eval.model_effect.visualization.render.wuji_retargeting_video import render_wuji_hand_video
        function = render_world_video if request['kind'] == 'mujoco' else render_wuji_hand_video
        with file_lock(folder / '.active.pin', shared=True), budget.reserve(output, max(2**20, len(ids) * request['width']**2 // 32)):
            def render(destination):
                function(world, cameras, kept, destination, fps=sample['fps'], intrinsics=K,
                    image_size=(sample['width'], sample['height']), width=request['width'],
                    label_text=request['label'] + ' | ' + request['space'], on_step=on_step)
            atomic_video_file(output, render, len(ids))
        atomic_json(folder / 'mapping.json', {'source_frame_indices': ids.tolist(),
            'timestamps_s': sample['timestamps_s'][request['start']:request['end']], 'coordinate_frame': request['space'],
            'display_camera': 'Native world trajectory' if request['space'] == 'world' else 'Identity used only for camera-coordinate display, not an estimated world trajectory',
            'scope': 'Visualization and kinematic IK only; immutable numerical prediction unchanged'})
        progress(status='completed', progress={'done': len(ids), 'total': len(ids)})
        return 0
    except BaseException as error:
        import traceback
        traceback.print_exc()
        progress(status='cancelled' if isinstance(error, (InterruptedError, KeyboardInterrupt)) else 'failed', error=str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
