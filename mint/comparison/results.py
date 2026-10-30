"""Frame-addressed reads, camera-source experiments and common metric scopes."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .contracts import CONNECTIONS, json_values, load_result, publish_result, transform_points


def public_metadata(metadata):
    value = {key: field for key, field in metadata.items() if key != 'provenance'}
    provenance = metadata.get('provenance', {})
    value['provenance_summary'] = {'source_root': provenance.get('source_root'),
        'native_source_files': len(provenance.get('sources', {})), 'assets': len(provenance.get('assets', {})),
        'full_manifest': '/api/comparison/runs/' + str(metadata.get('run_id', '')) + '/manifest'}
    return value


def aligned_distance(first, second, *, scale=False):
    """Least-squares proper rigid/similarity alignment of row-vector points."""
    first, second = np.asarray(first, float), np.asarray(second, float)
    x, y = first - first.mean(axis=0), second - second.mean(axis=0)
    U, singular, Vt = np.linalg.svd(x.T @ y)
    signs = np.ones(3); signs[-1] = np.linalg.det(U @ Vt)
    rotation = (U * signs) @ Vt
    factor = float(np.sum(singular * signs) / np.sum(x * x)) if scale and np.sum(x * x) > 1e-15 else 1.
    if factor <= 1e-12:
        factor = 1.  # Degenerate trajectories cannot establish a positive similarity scale.
    error = np.linalg.norm(factor * x @ rotation - y, axis=-1)
    return error, factor


def frame_payload(result_dir, sample, start=0, count=64, *, vertices=False, origin_source_frame=None):
    metadata, arrays = load_result(result_dir)
    start, count = int(start), int(count)
    if start < 0 or count < 1 or count > (8 if vertices else 256) or start >= sample['frames']:
        raise ValueError('Invalid frame range')
    source_ids = np.asarray(sample['source_frame_indices'][start:start + count], np.int64)
    ids = arrays['source_frame_indices']
    positions = np.searchsorted(ids, source_ids)
    present = positions < len(ids)
    present[present] &= ids[positions[present]] == source_ids[present]
    n = len(source_ids)
    payload = {'sample_positions': list(range(start, start + n)), 'source_frame_indices': source_ids,
               'metadata': public_metadata(metadata), 'connections': CONNECTIONS}
    for key in ('joints_camera_m', 'geometry_valid', 'K', 'presence', 'tracked', 'filled', 'confidence', 'exists_2d', 'exists_3d'):
        if key not in arrays:
            payload[key] = None
            continue
        source = arrays[key]
        shape = (n, *source.shape[1:])
        value = np.zeros(shape, bool) if source.dtype.kind == 'b' else np.full(shape, np.nan)
        value[present] = source[positions[present]]
        payload[key] = value
    payload['available'] = present
    if vertices and 'vertices_camera_m' in arrays:
        value = np.full((n, 2, 778, 3), np.nan)
        value[present] = arrays['vertices_camera_m'][positions[present]]
        payload['vertices_camera_m'] = value
        payload['vertices_world_m'] = None
    payload['joints_world_m'], payload['camera_world_m'] = None, None
    if 'T_world_camera' in arrays:
        T = np.full((n, 4, 4), np.nan)
        T[present] = arrays['T_world_camera'][positions[present]]
        rebase = np.eye(4)
        if origin_source_frame is not None:
            index = int(np.searchsorted(ids, origin_source_frame))
            if index >= len(ids) or ids[index] != origin_source_frame or not arrays['pose_valid'][index]:
                raise ValueError('The requested common world origin is unavailable in this run')
            rebase = np.linalg.inv(arrays['T_world_camera'][index])
        T = rebase @ T
        payload['joints_world_m'] = transform_points(T, payload['joints_camera_m'])
        payload['camera_world_m'] = T[:, :3, 3]
        if vertices and payload.get('vertices_camera_m') is not None:
            payload['vertices_world_m'] = transform_points(T, payload['vertices_camera_m'])
    return json_values(payload)


def trajectory(result_dir, *, maximum=1500, origin_source_frame=None):
    metadata, arrays = load_result(result_dir)
    n = len(arrays['timestamps_s'])
    indices = np.unique(np.rint(np.linspace(0, n - 1, min(n, maximum))).astype(int))
    wrists = arrays['joints_camera_m'][indices, :, 0].copy()
    camera, world = None, None
    if 'T_world_camera' in arrays:
        T = np.asarray(arrays['T_world_camera'][indices]).copy()
        if origin_source_frame is not None:
            position = int(np.searchsorted(arrays['source_frame_indices'], origin_source_frame))
            if position >= n or arrays['source_frame_indices'][position] != origin_source_frame or not arrays['pose_valid'][position]:
                raise ValueError('World origin unavailable')
            T = np.linalg.inv(arrays['T_world_camera'][position]) @ T
        world, camera = transform_points(T, wrists), T[:, :3, 3]
    return json_values({'source_frame_indices': arrays['source_frame_indices'][indices],
        'timestamps_s': arrays['timestamps_s'][indices], 'camera_wrists_m': wrists,
        'presence': arrays['presence'][indices] if 'presence' in arrays else None,
        'world_wrists_m': world, 'camera_world_m': camera, 'display_decimation_only': True,
        'world_frame': 'native' if origin_source_frame is None else f'camera_at_source_frame_{origin_source_frame}'})


def common_world_origin(results):
    available = []
    for path in results:
        _, arrays = load_result(path)
        if 'T_world_camera' in arrays:
            available.append(arrays['source_frame_indices'][arrays['pose_valid']])
    if not available:
        return None
    common = available[0]
    for ids in available[1:]:
        common = np.intersect1d(common, ids)
    return int(common[0]) if len(common) else None


def combine_camera(hand_dir, camera_dir, destination, *, metadata):
    hand_meta, hands = load_result(hand_dir)
    camera_meta, camera = load_result(camera_dir)
    if hand_meta['sample_id'] != camera_meta['sample_id']:
        raise ValueError('Camera-source experiments require the exact same input sample')
    if 'T_world_camera' not in camera:
        raise ValueError('The camera run has no world camera trajectory')
    source_ids = hands['source_frame_indices']
    indices = np.searchsorted(camera['source_frame_indices'], source_ids)
    matched = indices < len(camera['source_frame_indices'])
    matched[matched] &= camera['source_frame_indices'][indices[matched]] == source_ids[matched]
    T = np.full((len(source_ids), 4, 4), np.nan)
    valid = np.zeros(len(source_ids), bool)
    T[matched] = camera['T_world_camera'][indices[matched]]
    valid[matched] = camera['pose_valid'][indices[matched]]
    arrays = {k: np.asarray(v) for k, v in hands.items() if k not in {'T_world_camera', 'pose_valid'}}
    arrays.update(T_world_camera=T, pose_valid=valid)
    # Geometry in the optical camera is preserved exactly. Only the extrinsic source changes.
    return publish_result(destination, arrays, {**hand_meta, **metadata,
        'algorithm': 'camera_combination', 'component_sources': {'hands': hand_meta['run_id'], 'camera': camera_meta['run_id']},
        'camera_source': f"{camera_meta['label']} / {camera_meta['run_id']}",
        'fusion_scope': 'Extrinsic camera-source experiment; native hand geometry and K unchanged; no learned fusion',
        'extra_camera_frames_unavailable': int((~valid).sum())})


def paired_metrics(first_dir, second_dir):
    first_meta, a = load_result(first_dir)
    second_meta, b = load_result(second_dir)
    if first_meta['sample_id'] != second_meta['sample_id']:
        raise ValueError('Metrics require the same sample and optical coordinate system')
    common, ia, ib = np.intersect1d(a['source_frame_indices'], b['source_frame_indices'], return_indices=True)
    available_a, available_b = a['geometry_valid'][ia], b['geometry_valid'][ib]
    valid = available_a & available_b
    ja, jb = a['joints_camera_m'][ia], b['joints_camera_m'][ib]
    delta = np.linalg.norm(ja - jb, axis=-1) * 1000
    aligned = np.linalg.norm((ja - ja[:, :, :1]) - (jb - jb[:, :, :1]), axis=-1) * 1000
    reference = second_meta.get('reference_role') == 'user_declared_ground_truth'
    label = 'error_against_declared_GT' if reference else 'algorithm_discrepancy_not_accuracy'
    def mean(value): return float(np.mean(value)) if value.size else None
    result = {'scope': label, 'first_run': first_meta['run_id'], 'second_run': second_meta['run_id'],
        'common_time_frames': len(common), 'available_hand_slots_first': int(available_a.sum()),
        'available_hand_slots_second': int(available_b.sum()), 'paired_hand_slots': int(valid.sum()),
        'paired_joints': int(valid.sum() * 21), 'camera_absolute_joint_mm': mean(delta[valid]),
        'camera_wrist_mm': mean(delta[:, :, 0][valid]), 'wrist_aligned_joint_mm': mean(aligned[valid]),
        'missing_first_given_second': int((available_b & ~available_a).sum()),
        'metric_policy': 'Finite geometry intersection; presence threshold does not silently remove difficult predictions; coverage reported separately'}
    if valid.any():
        procrustes = [aligned_distance(x, y, scale=True)[0] for x, y in zip(ja[valid], jb[valid])]
        result['similarity_aligned_joint_mm'] = float(np.mean(procrustes) * 1000)
        result['joint_alignment'] = 'Per hand and frame, proper Sim3 fit; raw metric error and coverage remain separate'
    def project(joints, K):
        p = np.einsum('tij,thkj->thki', K, joints)
        return p[..., :2] / p[..., 2:3]
    positive = valid[:, :, None] & (ja[..., 2] > 1e-6) & (jb[..., 2] > 1e-6)
    with np.errstate(divide='ignore', invalid='ignore'):
        pixel_difference = np.linalg.norm(project(ja, a['K'][ia]) - project(jb, b['K'][ib]), axis=-1)
    result.update(paired_projectable_joints=int(positive.sum()),
        reprojection_difference_px=mean(pixel_difference[positive]),
        projection_policy='Each result uses its own declared K in the same sample pixel grid; positive depths only')
    dt = np.diff(a['timestamps_s'][ia])
    if len(dt):
        consecutive_valid = valid[:-1] & valid[1:]
        velocity_delta = np.linalg.norm(np.diff(ja[:, :, 0], axis=0) / dt[:, None, None]
            - np.diff(jb[:, :, 0], axis=0) / dt[:, None, None], axis=-1)
        result['camera_wrist_velocity_difference_m_s'] = mean(velocity_delta[consecutive_valid])
        result['paired_velocity_samples'] = int(consecutive_valid.sum())
    if 'T_world_camera' in a and 'T_world_camera' in b:
        pose_mask = a['pose_valid'][ia] & b['pose_valid'][ib]
        result['world_camera_common_frames'] = int(pose_mask.sum())
        if pose_mask.any():
            Ta, Tb = a['T_world_camera'][ia][pose_mask], b['T_world_camera'][ib][pose_mask]
            Ta = np.linalg.inv(Ta[0]) @ Ta; Tb = np.linalg.inv(Tb[0]) @ Tb
            result['first_camera_translation_rmse_m'] = float(np.sqrt(np.mean(np.sum((Ta[:, :3, 3] - Tb[:, :3, 3]) ** 2, axis=1))))
            result['world_alignment'] = 'first common camera frame only; no fitted SE3/Sim3 or GT scale'
            pa, pb = Ta[:, :3, 3], Tb[:, :3, 3]
            if len(pa) >= 3:
                rigid, _ = aligned_distance(pa, pb)
                sim, scale = aligned_distance(pa, pb, scale=True)
                result.update(camera_ate_se3_rmse_m=float(np.sqrt(np.mean(rigid**2))),
                    camera_ate_sim3_rmse_m=float(np.sqrt(np.mean(sim**2))), fitted_camera_scale=scale,
                    trajectory_alignment_policy='SE3 and fitted-scale Sim3 reported separately; GT role must be explicitly declared')
            if len(Ta) >= 2:
                relative_a = np.linalg.inv(Ta[:-1]) @ Ta[1:]
                relative_b = np.linalg.inv(Tb[:-1]) @ Tb[1:]
                error = np.linalg.inv(relative_b) @ relative_a
                angle = np.arccos(np.clip((np.trace(error[:, :3, :3], axis1=1, axis2=2) - 1) / 2, -1, 1))
                result.update(camera_rpe_translation_rmse_m=float(np.sqrt(np.mean(np.sum(error[:, :3, 3]**2, axis=1)))),
                    camera_rpe_samples=len(error),
                    camera_rpe_rotation_rmse_deg=float(np.rad2deg(np.sqrt(np.mean(angle**2)))),
                    rpe_policy='Between successive common valid camera samples, preserving original source times',
                    rpe_source_intervals_s=np.unique(np.round(np.diff(a['timestamps_s'][ia][pose_mask]), 6)).tolist())
    return json_values(result)
