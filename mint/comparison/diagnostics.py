"""Count-weighted error breakdowns; geometry availability is not detection recall."""
from __future__ import annotations

import numpy as np

from .contracts import json_values, load_result


def error_breakdown(prediction, reference, *, image_hw=None):
    common, a, b = np.intersect1d(prediction['source_frame_indices'], reference['source_frame_indices'], return_indices=True)
    if not np.allclose(prediction['timestamps_s'][a], reference['timestamps_s'][b], atol=1e-5, rtol=1e-6):
        raise ValueError('Breakdown result timestamps do not match the reference timeline')
    # Reference-only rows stay in the denominator. Intersecting before this
    # point would make a sparse prediction appear to have complete coverage.
    g = np.asarray(reference['joints_camera_m'], float)
    gt = np.asarray(reference['geometry_valid'], bool)
    p = np.full_like(g, np.nan)
    geometry = np.zeros_like(gt)
    detected = np.zeros_like(gt)
    p[b] = prediction['joints_camera_m'][a]
    geometry[b] = prediction['geometry_valid'][a]
    detected[b] = geometry[b] & prediction.get('presence', prediction['geometry_valid'])[a]
    paired = geometry & gt
    delta = np.linalg.norm(p - g, axis=-1) * 1000
    relative = np.linalg.norm((p - p[:, :, :1]) - (g - g[:, :, :1]), axis=-1) * 1000
    K = np.broadcast_to(np.eye(3), (len(g), 3, 3)).copy()
    K[b] = prediction['K'][a]
    pp = np.einsum('tij,thkj->thki', K, p)
    gp = np.einsum('tij,thkj->thki', reference['K'], g)
    with np.errstate(divide='ignore', invalid='ignore'):
        pxy = pp[..., :2] / pp[..., 2:]
        gxy = gp[..., :2] / gp[..., 2:]
    pixel = np.linalg.norm(pxy - gxy, axis=-1)
    front = (p[..., 2] > 1e-6) & (g[..., 2] > 1e-6)

    def stats(mask):
        valid = mask[..., None] & paired[..., None] & np.isfinite(delta)
        pixels = valid & front & np.isfinite(pixel)
        wrist = mask & paired & np.isfinite(p[:, :, 0, 2]) & np.isfinite(g[:, :, 0, 2])
        labels = mask & gt
        def value(x, m):
            return {'mean': float(np.mean(x[m])) if m.any() else None, 'count': int(m.sum())}
        return {'gt_hand_frames': int(labels.sum()), 'paired_hand_frames': int((mask & paired).sum()),
                'geometry_coverage': float((mask & paired).sum() / labels.sum()) if labels.any() else None,
                'detection_recall_on_labelled_positive_hands': float((mask & gt & detected).sum() / labels.sum()) if labels.any() else None,
                'detection_precision': None,
                'absolute_joint_mm': value(delta, valid), 'wrist_aligned_joint_mm': value(relative, valid),
                'reprojection_px': value(pixel, pixels),
                'wrist_depth_abs_mm': value(np.abs(p[:, :, 0, 2] - g[:, :, 0, 2]) * 1000, wrist)}

    masks = {'all': np.ones(gt.shape, bool)}
    for slot, side in enumerate(('left', 'right')):
        masks[side] = np.broadcast_to(np.arange(2) == slot, gt.shape)
    depth = g[:, :, 0, 2]
    for name, lo, hi in [('depth_0_30cm', 0, .3), ('depth_30_60cm', .3, .6), ('depth_60_100cm', .6, 1), ('depth_ge_100cm', 1, np.inf)]:
        masks[name] = (depth >= lo) & (depth < hi)
    if image_hw:
        h, w = image_hw
        on_screen = (g[..., 2] > 1e-6) & (gxy[..., 0] >= 0) & (gxy[..., 0] < w) & (gxy[..., 1] >= 0) & (gxy[..., 1] < h)
        masks['all_gt_joints_in_frame'] = on_screen.all(axis=-1)
        masks['some_gt_joints_out_of_frame'] = ~on_screen.all(axis=-1)
    per_joint = []
    for joint in range(21):
        good = paired & np.isfinite(delta[:, :, joint])
        per_joint.append({'joint': joint, 'paired_hand_frames': int(good.sum()),
                          'absolute_mm': float(delta[:, :, joint][good].mean()) if good.any() else None,
                          'wrist_aligned_mm': float(relative[:, :, joint][good].mean()) if good.any() else None})
    return json_values({'aligned_frames': len(common), 'prediction_frames': len(prediction['source_frame_indices']),
        'reference_frames': len(reference['source_frame_indices']), 'groups': {k: stats(v) for k, v in masks.items()},
        'joints': per_joint, 'policy': {
            'detection': 'Recall on valid labelled positive hand frames only; unlabelled slots are unknown, so precision is unavailable',
            'errors': 'Finite paired geometry, independently of predicted presence; all labelled reference frames remain in the coverage denominator, including absent output rows',
            'visibility': 'Derived image-boundary groups; not an occlusion annotation or a visible-joint benchmark',
            'reprojection': 'Each result uses its own declared pixel matrix; grids must match',
            'units': '3D millimetres; image pixels; camera coordinates'}})


def run_breakdown(manager, run_id, reference_run):
    first, second = manager.run(run_id), manager.run(reference_run)
    if run_id == reference_run or first['sample_id'] != second['sample_id'] or not first['result'] or not second['result']:
        raise ValueError('Choose two published results on the same input')
    first_metadata, a = load_result(manager.root / 'runs' / run_id / 'result')
    if first_metadata.get('reference_role') == 'user_declared_ground_truth':
        raise ValueError('Choose a prediction as the primary breakdown result, not a ground-truth reference')
    metadata, b = load_result(manager.root / 'runs' / reference_run / 'result')
    if first_metadata['sample_id'] != metadata['sample_id'] or first_metadata['sample_id'] != first['sample_id']:
        raise ValueError('Breakdown result metadata belongs to a different input sample')
    import json
    sample = json.loads((manager.root / 'inputs' / first['sample_id'] / 'sample.json').read_text())
    result = error_breakdown(a, b, image_hw=(sample['height'], sample['width']))
    return {**result, 'run_id': run_id, 'reference_run': reference_run,
            'comparison_role': 'declared_GT_error' if metadata.get('reference_role') == 'user_declared_ground_truth' else 'prediction_discrepancy'}
