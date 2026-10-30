"""Run original model functions with an explicit, lossless input bridge."""
from __future__ import annotations

import json
import os
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from mint.runtime import atomic_json, atomic_npz, digest, file_identity
from .normalizers import ace_geometry, hawor_camera_geometry, hawor_world_geometry, mint_geometry


def stage_cached(ctx, name):
    try:
        state = json.loads((ctx.work / (name + '.complete.json')).read_text())
        return state['run_id'] == ctx.run_id and all(file_identity(p) == identity for p, identity in state['files'].items())
    except (OSError, ValueError, KeyError):
        return False


def publish_stage(ctx, name, paths):
    records = {str(path): file_identity(path) for path in paths}
    if not records or any(value.get('missing') for value in records.values()):
        raise ValueError('Cannot publish an incomplete native stage: ' + name)
    atomic_json(ctx.work / (name + '.complete.json'), {'run_id': ctx.run_id, 'files': records})


def run_mint(ctx):
    from mint.inference.engine import StudentEngine
    from mint.inference.base import InferenceCancelled
    ctx.progress(stage='model_load')
    options, spec, sample = ctx.options, ctx.spec, ctx.sample
    root = Path(spec['root'])
    dynamic = ctx.request.get('dynamic_gpus', False)
    devices = options['resource_policy'] if dynamic else 'auto'
    started = time.perf_counter()
    engine = StudentEngine(str(root / 'configs/training/mint_step2.yaml'),
        ckpt=str(root / 'checkpoints/model.safetensors'), devices=devices, compile_mode='off', fp8_mode='off')
    ctx.timings['model_load_seconds'] = time.perf_counter() - started
    try:
        started = time.perf_counter()
        def step(done, total):
            ctx.check_cancel()
            ctx.progress(stage='inference', done=done, total=total,
                         gpu_scheduler=getattr(engine, 'gpu_scheduler_status', {}))
        prediction = engine.predict(ctx.frames, cam_mode=options['mode'], hand_mode=options['hand_mode'],
            cache_identity={'comparison_sample': sample['id'], 'frames': sample['source_frame_indices']},
            task_parameters={'kind': 'comparison', 'run_id': ctx.run_id},
            cancel_check=ctx.cancelled, on_step=step)
        ctx.timings['native_inference_seconds'] = time.perf_counter() - started
        atomic_npz(ctx.work / 'mint_native.npz', **{k: np.asarray(v) for k, v in prediction.items() if not k.startswith('_')})
        ctx.metadata.update(native_timings=prediction.get('_timings'), checkpoint_task=prediction.get('_task'),
            camera_source='MINT predicted camera', K_source='MINT native Viewer first-frame FoV',
            confidence_semantics='sigmoid native hand presence logit; geometry retained independently',
            postprocess=options['hand_mode'], numerical_path='Unchanged StudentEngine and original MANO decoder')
        ctx.progress(stage='mano_decode')
        arrays = mint_geometry(prediction, sample)
    finally:
        engine.close()
    return arrays


def _ace_size(width, height, cap):
    target = min(width, cap)
    return max(32, round(target / 32) * 32), max(32, round(height * target / width / 32) * 32)


def run_ace(ctx):
    import cv2
    import torch
    import yaml
    from ace_ego_hand.models.geodit_model import GeoDitModel
    from ace_ego_hand.video_vae import load_vae, encode
    from ace_ego_hand.inference import predict_video, DUMP_KEYS
    from ace_ego_hand.long_video import build_segments, merge_records
    sample, options, root = ctx.sample, ctx.options, Path(ctx.spec['root'])
    n = sample['frames']
    if n < 5:
        raise ValueError('ACE requires at least 5 frames; no implicit frame padding is performed')
    given = ctx.request['algorithm'] == 'ace_kgiven'
    if given and not sample['camera']:
        raise ValueError('ACE K-given requires declared undistorted pinhole calibration')
    width, height = _ace_size(sample['width'], sample['height'], options['encode_width'])
    if given:
        K = np.asarray(sample['camera']['K'], np.float64).copy()
        K[0] *= width / sample['width']; K[1] *= height / sample['height']
        intr = {'fx': K[0, 0], 'fy': K[1, 1], 'cx': K[0, 2], 'cy': K[1, 2],
                'image_width': width, 'image_height': height, 'source': sample['camera']['source']}
    else:
        focal = .5 * width / np.tan(np.deg2rad(30.))
        intr = {'fx': focal, 'fy': focal, 'cx': width / 2, 'cy': height / 2,
                'image_width': width, 'image_height': height,
                'source': 'nominal placeholder; K-free geometry uses predicted rays, not this K'}
    independent = options['mode'] == 'independent81'
    if independent and n < 81:
        raise ValueError('Independent 81-frame mode needs at least 81 frames; select native_tiled for a short clip')
    used = n if independent else 4 * ((n - 1) // 4) + 1
    segments = build_segments(n, options['stride']) if independent else [
        {'index': 0, 'start': 0, 'end': used, 'frame_count': used, 'keep_start': 0, 'keep_end': used}]
    records = [None] * len(segments)
    missing = []
    for index, segment in enumerate(segments):
        path = ctx.work / f'ace_segment_{index:06d}.npz'
        try:
            with np.load(path, allow_pickle=False) as z:
                value = {key: z[key].copy() for key in z.files}
            if str(value.pop('run_id')) != ctx.run_id or not np.array_equal(value['frame_indices'], np.arange(segment['start'], segment['end'])):
                raise ValueError('Segment cache identity differs')
            k = value.pop('K_matrix')
            count = segment['end'] - segment['start']
            shapes = {'go': (count,2,1,3,3), 'hp': (count,2,15,3,3), 'betas': (count,2,10),
                'cam_trans': (count,2,3), 'is_right': (count,2), 'exists_2d': (count,2), 'exists_3d': (count,2),
                'joints_2d': (count,2,21,2), 'joints_cam_direct': (count,2,21,3), 'wrist_cam_direct': (count,2,3)}
            if any(np.asarray(value[key]).shape != shape for key, shape in shapes.items()) or k.shape != (3,3) or not np.isfinite(k).all() or min(k[0,0],k[1,1]) <= 0:
                raise ValueError('Malformed native ACE cache layout')
            if not all(np.isfinite(v).all() for v in value.values()):
                raise ValueError('Nonfinite ACE cache')
            value['pred_intrinsics'] = value['intrinsics'] = {'fx': float(k[0, 0]), 'fy': float(k[1, 1]),
                'cx': float(k[0, 2]), 'cy': float(k[1, 2]), 'image_width': width, 'image_height': height}
            records[index] = value
        except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
            missing.append(index)
    ctx.metadata['reused_segments'] = len(segments) - len(missing)
    ctx.metadata['new_segments'] = len(missing)
    if missing and ctx.request.get('external_pool'):
        from .native_pool import NativePool
        if getattr(ctx,'_ace_pool_finished',False):
            raise ValueError('A parallel ACE segment failed cache validation')
        original_reused = len(segments)-len(missing)
        with NativePool(ctx,'ace',maximum=min(options['max_gpus'],len(missing))) as pool:
            pool.run([segments[i] for i in missing],stage='parallel_inference')
        ctx.timings['native_parallel_wall_seconds']=ctx.metadata['parallel_stages']['ace']['wall_seconds']
        ctx.timings['native_worker_sum_seconds']=ctx.metadata['parallel_stages']['ace']['native_worker_seconds']
        # Re-enter only the CPU load/merge/decode path after all atomic native segments exist.
        ctx._ace_pool_finished=True
        result=run_ace(ctx)
        ctx.metadata.update(reused_segments=original_reused,new_segments=len(missing))
        return result
    elif missing:
            ctx.progress(stage='model_load', done=0, total=len(segments))
            config = root / ('options/ace_ego_hand_k.yml' if given else 'options/ace_ego_hand_kfree.yml')
            weights = root / ('checkpoints/ace_ego_hand_k.pt' if given else 'checkpoints/ace_ego_hand_kfree.pt')
            opt = yaml.safe_load(config.read_text())
            # The model's unchanged loader resolves the configured paths relative to its native cwd.
            started = time.perf_counter()
            device = torch.device('cuda:0')
            model = GeoDitModel(opt, device)
            model.load_inference(str(weights))
            vae = load_vae(device)
            ctx.timings['model_load_seconds'] = time.perf_counter() - started
            try:
                for index in missing:
                    ctx.check_cancel()
                    segment = segments[index]
                    start, stop = segment['start'], segment['end']
                    ctx.progress(stage='vae_encode', done=index, total=len(segments))
                    tick = time.perf_counter()
                    # Identical OpenCV INTER_AREA, normalization and tensor layout to video_vae.decode_video.
                    pixels = np.stack([cv2.resize(np.asarray(frame), (width, height), interpolation=cv2.INTER_AREA)
                                       if frame.shape[:2] != (height, width) else np.asarray(frame)
                                       for frame in ctx.frames[start:stop]])
                    pixels = torch.from_numpy(pixels.astype(np.float32) / 255. * 2. - 1.).permute(0, 3, 1, 2).contiguous()
                    ctrl = encode(vae, pixels, device).float()
                    del pixels
                    torch.cuda.empty_cache()
                    ctx.progress(stage='hand_prediction', done=index, total=len(segments))
                    prediction = predict_video(model, 15, ctrl, intr, 'tiled', device, tile_w=22)
                    torch.cuda.synchronize(device)
                    native = {key: np.asarray(prediction[key], np.float32) for key in DUMP_KEYS}
                    count = stop - start
                    record = {'go': native['global_orient'].reshape(count, 2, 1, 3, 3), 'hp': native['hand_pose'],
                        'betas': native['betas'], 'cam_trans': native['cam_trans'],
                        'is_right': np.tile(np.array([0., 1.], np.float32), (count, 1)),
                        'exists_2d': native['exists_2d'], 'exists_3d': native['exists_3d'],
                        'joints_2d': native['direct_joints2d'], 'joints_cam_direct': native['direct_joints_cam'],
                        'wrist_cam_direct': native['direct_wrist_cam'], 'frame_indices': np.arange(start, stop)}
                    camera = intr if given else prediction.get('_pred_K')
                    if camera is None:
                        raise ValueError('K-free returned no predicted intrinsics; placeholder K cannot render the prediction')
                    k = np.array([[camera['fx'], 0, camera['cx']], [0, camera['fy'], camera['cy']], [0, 0, 1.]])
                    atomic_npz(ctx.work / f'ace_segment_{index:06d}.npz', **record, K_matrix=k, run_id=np.asarray(ctx.run_id))
                    record['intrinsics'] = record['pred_intrinsics'] = camera
                    records[index] = record
                    ctx.timings['native_inference_seconds'] = ctx.timings.get('native_inference_seconds', 0) + time.perf_counter() - tick
                    del ctrl, prediction
                    ctx.cooperate(tick)
            finally:
                del model, vae
                torch.cuda.empty_cache()
    if independent:
        merged = merge_records(records, segments, n, options['merge'], 'intrinsics' if given else 'pred_intrinsics')
        K = merged['pred_camera_K'].copy()
    else:
        merged = records[0]
        camera = merged['intrinsics']
        K = np.broadcast_to(np.array([[camera['fx'], 0, camera['cx']], [0, camera['fy'], camera['cy']], [0, 0, 1.]]), (used, 3, 3)).copy()
    K[:, 0] *= sample['width'] / width; K[:, 1] *= sample['height'] / height
    ctx.progress(stage='mano_decode', done=used, total=n)
    arrays = ace_geometry(merged, sample, K, np.arange(used))
    if independent:
        arrays['segment_indices'] = merged['segment_indices']
    ctx.metadata.update(K_source=sample['camera']['source'] if given else 'ACE predicted ray-field intrinsics',
        camera_source='unavailable: ACE has no native world camera', native_mode=options['mode'],
        encoded_size=[width, height], trim_tail_frames=n - used,
        merge_method=options['merge'] if independent else 'native tiled latent windows',
        postprocess='native independent VAE clips; not equivalent to full-sequence VAE' if independent else 'native full-sequence VAE + tiled predictions',
        confidence_semantics='native exists_3d; exists_2d is preserved separately',
        numerical_path='Original VAE, GeoDitModel, predict_video, merge_records and MANO decode')
    return arrays


def _hawor_numpy_compat():
    for name, value in {'bool': np.bool_, 'int': int, 'float': float, 'complex': complex,
                        'object': object, 'str': str, 'unicode': str}.items():
        if name not in vars(np):
            setattr(np, name, value)


def run_hawor(ctx):
    if ctx.request.get('external_pool'):
        from .hawor_parallel import run_hawor_parallel
        return run_hawor_parallel(ctx)
    import cv2
    import torch
    import joblib
    _hawor_numpy_compat()
    from scripts.scripts_test_video.detect_track_video import detect_track_video
    from scripts.scripts_test_video.hawor_video import hawor_motion_estimation, hawor_infiller
    from scripts.scripts_test_video.hawor_slam import hawor_slam
    from lib.eval_utils.custom_utils import load_slam_cam
    sample, options = ctx.sample, ctx.options
    native_w = min(sample['width'], options['analysis_width']) if options['analysis_width'] else sample['width']
    native_h = max(2, round(sample['height'] * native_w / sample['width']))
    sx, sy = native_w / sample['width'], native_h / sample['height']
    if sample['camera']:
        K_source = np.asarray(sample['camera']['K'], np.float64)
        K_grid = K_source.copy(); K_grid[0] *= sx; K_grid[1] *= sy
        focal = float(np.sqrt(K_grid[0, 0] * K_grid[1, 1]))
        k_source = sample['camera']['source']
    elif options['focal'] is not None:
        focal = options['focal'] * sx
        K_source = np.array([[options['focal'], 0, sample['width'] / 2], [0, options['focal'], sample['height'] / 2], [0, 0, 1.]])
        K_grid = K_source.copy(); K_grid[0] *= sx; K_grid[1] *= sy
        k_source = 'User-declared focal in sample pixels (assumed centered isotropic pinhole)'
    else:
        focal = 600.
        K_grid = np.array([[focal, 0, native_w / 2], [0, focal, native_h / 2], [0, 0, 1.]])
        K_source = K_grid.copy(); K_source[0] /= sx; K_source[1] /= sy
        k_source = 'HaWoR native 600px fallback at analysis grid; not measured calibration'
    canonical_K = np.array([[focal, 0, native_w / 2], [0, focal, native_h / 2], [0, 0, 1.]])
    homography = canonical_K @ np.linalg.inv(K_grid)
    sequence = ctx.work / 'input'
    images = sequence / 'extracted_images'
    images.mkdir(parents=True, exist_ok=True)
    # The original function allocates a float64 full-sequence mask volume. Refuse a
    # resource-incompatible request rather than truncate frames or alter its arithmetic.
    mask_bytes = sample['frames'] * native_h * native_w * 8
    available = next((int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                      if line.startswith('MemAvailable:')), 0) if Path('/proc/meminfo').exists() else 0
    if available and mask_bytes + 12 * 2**30 > available * .70:
        raise MemoryError('HaWoR native mask volume exceeds the safe host memory budget; lower analysis_width explicitly or run on a host with more free RAM')
    ctx.progress(stage='input_bridge', done=0, total=sample['frames'])
    bridge_ready = stage_cached(ctx, 'frames')
    for index, frame in enumerate(ctx.frames):
        ctx.check_cancel()
        destination = images / f'{index:06d}.jpg'
        if not bridge_ready:
            resized = cv2.resize(np.asarray(frame), (native_w, native_h), interpolation=cv2.INTER_AREA) if frame.shape[:2] != (native_h, native_w) else np.asarray(frame)
            remapped = cv2.warpPerspective(resized, homography, (native_w, native_h),
                flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            ok, encoded = cv2.imencode('.png', cv2.cvtColor(remapped, cv2.COLOR_RGB2BGR))
            if not ok:
                raise RuntimeError('Lossless native frame materialization failed')
            temporary = destination.with_suffix('.partial')
            temporary.write_bytes(encoded.tobytes()); os.replace(temporary, destination)
        if index % 48 == 0:
            import shutil
            if shutil.disk_usage(ctx.work).free < float(os.environ.get('MINT_CACHE_MIN_FREE_GIB', '16')) * 2**30 + 512 * 2**20:
                raise OSError('HaWoR work directory reached the disk free-space protection line')
            ctx.progress(stage='input_bridge', done=index, total=sample['frames'])
    if not bridge_ready:
        publish_stage(ctx, 'frames', sorted(images.glob('*.jpg')))
    args = SimpleNamespace(video_path=str(ctx.work / 'input.mp4'), input_type='file', img_focal=focal,
        checkpoint=str(Path(ctx.spec['root']) / 'weights/hawor/checkpoints/hawor.ckpt'),
        infiller_weight=str(Path(ctx.spec['root']) / 'weights/hawor/checkpoints/infiller.pt'))
    ctx.progress(stage='detection_tracking')
    tracks = sequence / f"tracks_0_{sample['frames']}"
    if not stage_cached(ctx, 'detection'):
        for name in ('model_boxes.npy', 'model_tracks.npy'):
            (tracks / name).unlink(missing_ok=True)
    tick = time.perf_counter()
    begin, end, seq, imgfiles = detect_track_video(args)
    if begin != 0 or end != sample['frames'] or len(imgfiles) != sample['frames']:
        raise ValueError('Native HaWoR input bridge changed frame coverage')
    ctx.timings['detection_tracking_seconds'] = time.perf_counter() - tick
    publish_stage(ctx, 'detection', [tracks / 'model_boxes.npy', tracks / 'model_tracks.npy'])
    ctx.cooperate(tick)
    ctx.progress(stage='hand_prediction')
    if not stage_cached(ctx, 'motion'):
        (tracks / 'frame_chunks_all.npy').unlink(missing_ok=True)
    tick = time.perf_counter()
    with torch.no_grad():
        chunks, _ = hawor_motion_estimation(args, begin, end, seq)
    ctx.timings['hand_prediction_seconds'] = time.perf_counter() - tick
    publish_stage(ctx, 'motion', [tracks / 'frame_chunks_all.npy', tracks / 'model_masks.npy',
        *[Path(seq) / 'cam_space' / str(slot) / f'{frames[0]}_{frames[-1]}.json'
          for slot, batches in chunks.items() for frames in batches]])
    ctx.cooperate(tick)
    ctx.progress(stage='camera_geometry')
    arrays = hawor_camera_geometry(seq, chunks, sample, K_source)
    ctx.metadata.update(K_source=k_source, native_analysis_size=[native_w, native_h],
        input_camera_remap=homography.tolist(), input_encoding='lossless PNG bytes under native *.jpg glob; no 30 FPS resampling',
        tracked_semantics='Native motion chunks after detection/tracking and bbox interpolation; not raw detector confidence',
        confidence_semantics='Unavailable; binary tracking/infiller validity is not a calibrated probability',
        numerical_path='Unchanged detect_track_video, hawor_motion_estimation, SLAM, infiller and native MANO',
        camera_source='unavailable: camera-only stage', stage_coverage={'camera': True, 'world': False})
    if ctx.request['algorithm'] == 'hawor_camera':
        return arrays
    try:
        ctx.progress(stage='slam_scale')
        tick = time.perf_counter()
        slam = Path(seq) / 'SLAM' / f'hawor_slam_w_scale_{begin}_{end}.npz'
        if not stage_cached(ctx, 'slam'):
            slam.unlink(missing_ok=True)
            with torch.no_grad():
                hawor_slam(args, begin, end)
            with np.load(slam, allow_pickle=False) as values:
                if values['traj'].shape[0] != sample['frames'] or not np.isfinite(values['traj']).all() or not float(values['scale']) > 0:
                    raise ValueError('SLAM output has incomplete coverage, invalid pose or nonpositive scale')
            publish_stage(ctx, 'slam', [slam])
        ctx.timings['slam_scale_seconds'] = time.perf_counter() - tick
        ctx.cooperate(tick)
        ctx.progress(stage='world_infiller')
        tick = time.perf_counter()
        with torch.no_grad():
            trans, rotation, hand, shape, valid = hawor_infiller(args, begin, end, chunks)
        _, _, camera_rotation, camera_translation = load_slam_cam(str(slam))
        def cpu(value): return value.detach().cpu().numpy() if torch.is_tensor(value) else np.asarray(value)
        native = {'translation': cpu(trans), 'root_rotation': cpu(rotation), 'hand_pose': cpu(hand),
                  'shape': cpu(shape), 'valid': cpu(valid), 'camera_rotation_c2w': cpu(camera_rotation),
                  'camera_translation_c2w': cpu(camera_translation), 'hand_order': np.asarray(['left', 'right']),
                  'img_focal': np.asarray(focal)}
        atomic_npz(ctx.work / 'hawor_world_native.npz', **native)
        ctx.timings['infiller_seconds'] = time.perf_counter() - tick
        ctx.check_cancel()
        result = hawor_world_geometry(native, sample, K_source, observed=arrays['tracked'])
        ctx.metadata.update(camera_source='HaWoR masked DROID-SLAM + Metric3D metric scale',
                            stage_coverage={'camera': True, 'world': True},
                            postprocess='Native world infiller; filled mask retained separately')
        return result
    except Exception as exc:
        ctx.check_cancel()
        ctx.metadata.update(partial=True, stage_error={'stage': 'slam_or_infiller', 'error': str(exc)},
            camera_source='unavailable: full pipeline failed after successful camera stage',
            postprocess='Camera-stage result retained; no synthetic world trajectory or infiller substitute')
        return arrays


def import_native(ctx):
    """Read trusted server-native files only inside the matching algorithm environment."""
    import pickle
    entry = ctx.request['import']
    source = Path(entry['path'])
    if file_identity(source) != entry['identity']:
        raise ValueError('Native import source changed')
    positions = np.asarray(entry['frame_map'], np.int64)
    if entry['format'] == 'ace_pickle':
        root = Path(ctx.spec['root'])
        if not source.resolve().is_relative_to(root / 'results'):
            raise ValueError('Pickle imports are restricted to the configured ACE results directory')
        with source.open('rb') as stream:
            value = pickle.load(stream)
    else:
        with np.load(source, allow_pickle=False) as z:
            value = {k: z[k].copy() for k in z.files}
    ctx.metadata.update(import_source=entry, import_alignment='Explicit user-declared sample positions',
                        performance_scope='Native result import and decode only; excludes original RGB inference time')
    if entry.get('reference_role'):
        ctx.metadata.update(reference_role=entry['reference_role'], K_source=entry['declared_source'],
            camera_source='User-declared reference camera' if 'T_world_camera' in value else 'Unavailable in reference')
    if entry['format'] == 'mint_npz':
        ctx.metadata.update(camera_source='Imported MINT predicted camera', K_source='MINT native first-frame FoV')
        return mint_geometry(value, ctx.sample, positions)
    if entry['format'] == 'ace_pickle':
        camera = value.get('pred_intrinsics') if ctx.request['algorithm'] == 'ace_kfree' else value.get('intrinsics')
        if not camera:
            raise ValueError('Native ACE import has no appropriate projection intrinsics')
        K = np.array([[camera['fx'], 0, camera['cx']], [0, camera['fy'], camera['cy']], [0, 0, 1.]])
        K[0] *= ctx.sample['width'] / camera['image_width']; K[1] *= ctx.sample['height'] / camera['image_height']
        return ace_geometry(value, ctx.sample, K, positions)
    if entry['format'] == 'hawor_world_npz':
        _hawor_numpy_compat()
        focal = float(value['img_focal'])
        w, h = entry['image_size']
        K = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1.]])
        K[0] *= ctx.sample['width'] / w; K[1] *= ctx.sample['height'] / h
        ctx.metadata.update(camera_source='Imported HaWoR native world camera', K_source='Imported HaWoR img_focal',
            tracked_semantics='Unavailable for this imported file; valid includes infiller frames')
        return hawor_world_geometry(value, ctx.sample, K, positions)
    if entry['format'] == 'normalized_npz':
        from .normalizers import timeline
        n = len(value['joints_camera_m'])
        if len(positions) != n:
            raise ValueError('Explicit normalized import frame map differs')
        value.update(timeline(ctx.sample, positions))
        return value
    raise ValueError('Unsupported native import format')
