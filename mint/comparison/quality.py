"""Frame-level diagnostics and review candidates, separate from ground truth."""
from __future__ import annotations

import json
import time

import numpy as np

from mint.runtime import atomic_json, atomic_npz, digest, file_identity
from mint.inference.cache_budget import file_lock
from .contracts import load_result, json_values


def frame_diagnostics(arrays, reference=None):
    ids = np.asarray(arrays['source_frame_indices'])
    times = np.asarray(arrays['timestamps_s'])
    n = len(ids)
    visible = np.asarray(arrays['geometry_valid']).copy()
    if 'presence' in arrays:
        visible &= arrays['presence']
    output = {'source_frame_indices': ids, 'timestamps_s': times, 'visible_hands': visible.sum(axis=1),
              'joint_difference_mm': np.full(n, np.nan), 'reprojection_difference_px': np.full(n, np.nan),
              'wrist_acceleration_m_s2': np.full(n, np.nan), 'paired_joints': np.zeros(n, np.int64)}
    # Bounded chunks avoid copying all joints/vertices for a long recording.
    if reference is not None:
        _, ia, ib = np.intersect1d(ids, reference['source_frame_indices'], return_indices=True)
        for start in range(0, len(ia), 2048):
            a, b = ia[start:start + 2048], ib[start:start + 2048]
            ja, jb = np.asarray(arrays['joints_camera_m'][a]), np.asarray(reference['joints_camera_m'][b])
            valid = arrays['geometry_valid'][a] & reference['geometry_valid'][b]
            count = valid.sum(axis=1) * 21
            diff = np.linalg.norm(ja - jb, axis=-1) * 1000
            output['joint_difference_mm'][a] = np.divide(np.where(valid[..., None], diff, 0).sum(axis=(1, 2)), count,
                out=np.full(len(a), np.nan), where=count > 0)
            output['paired_joints'][a] = count
            pa = np.einsum('tij,thkj->thki', arrays['K'][a], ja)
            pb = np.einsum('tij,thkj->thki', reference['K'][b], jb)
            positive = valid[..., None] & (ja[..., 2] > 1e-6) & (jb[..., 2] > 1e-6)
            delta = np.linalg.norm(pa[..., :2] / np.maximum(pa[..., 2:], 1e-6) - pb[..., :2] / np.maximum(pb[..., 2:], 1e-6), axis=-1)
            count2 = positive.sum(axis=(1, 2))
            output['reprojection_difference_px'][a] = np.divide(np.where(positive, delta, 0).sum(axis=(1, 2)), count2,
                out=np.full(len(a), np.nan), where=count2 > 0)
    if n >= 3:
        for start in range(0, n - 2, 2048):
            end = min(n - 2, start + 2048)
            wrists = np.asarray(arrays['joints_camera_m'][start:end + 2, :, 0])
            dt = np.diff(times[start:end + 2])
            velocity = np.diff(wrists, axis=0) / dt[:, None, None]
            acceleration = np.linalg.norm(np.diff(velocity, axis=0) / ((dt[1:] + dt[:-1])[:, None, None] / 2), axis=-1)
            mask = visible[start:end] & visible[start + 1:end + 1] & visible[start + 2:end + 2]
            count = mask.sum(axis=1)
            output['wrist_acceleration_m_s2'][start + 1:end + 1] = np.divide(np.where(mask, acceleration, 0).sum(axis=1), count,
                out=np.full(end - start, np.nan), where=count > 0)
    return output


def issue_intervals(curves, *, joint_mm=100., acceleration=30., reference=False):
    values = [('no_visible_geometry', curves['visible_hands'] == 0)]
    if 'joint_difference_mm' in curves:
        values.append(('joint_error_against_declared_GT' if reference else 'joint_discrepancy', curves['joint_difference_mm'] > joint_mm))
    values.append(('motion_acceleration_candidate', curves['wrist_acceleration_m_s2'] > acceleration))
    issues = []
    for reason, mask in values:
        boundaries = np.diff(np.r_[False, mask, False].astype(np.int8))
        for start, end in zip(np.flatnonzero(boundaries == 1), np.flatnonzero(boundaries == -1)):
            issues.append({'start': int(start), 'end': int(end), 'source_start': int(curves['source_frame_indices'][start]),
                'source_end': int(curves['source_frame_indices'][end - 1]), 'reason': reason,
                'interpretation': 'review_candidate; no automatic label or ground-truth promotion'})
    return sorted(issues, key=lambda item: (item['start'], item['reason']))


class QualityService:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.root / 'quality'
        self.root.mkdir(exist_ok=True)

    def create(self, run_id, reference_run=None, *, joint_mm=100., acceleration=30.):
        if not (0 < float(joint_mm) <= 10000 and 0 < float(acceleration) <= 100000):
            raise ValueError('Choose finite positive quality thresholds')
        run = self.manager.run(run_id, summary=True)
        if reference_run == run_id:
            raise ValueError('Choose distinct prediction and reference results')
        other = self.manager.run(reference_run, summary=True) if reference_run else None
        if not run['result'] or other and not other['result']:
            raise ValueError('Quality diagnostics require published results')
        if other and other['sample_id'] != run['sample_id']:
            raise ValueError('Compare results on the same input timeline')
        path = self.manager.root / 'runs' / run_id / 'result'
        rpath = self.manager.root / 'runs' / reference_run / 'result' if other else None
        identity = {'schema': 1, 'run_id': run_id, 'reference_run': reference_run,
                    'result': file_identity(path / 'result.json'), 'reference': file_identity(rpath / 'result.json') if rpath else None,
                    'joint_mm': float(joint_mm), 'acceleration': float(acceleration)}
        key = digest(identity)
        with file_lock(self.root / (key + '.lock')):
            if not (self.root / (key + '.json')).exists():
                metadata, arrays = load_result(path)
                reference = load_result(rpath) if rpath else None
                curves = frame_diagnostics(arrays, reference[1] if reference else None)
                is_gt = bool(reference and reference[0].get('reference_role') == 'user_declared_ground_truth')
                issues = issue_intervals(curves, joint_mm=float(joint_mm), acceleration=float(acceleration), reference=is_gt)
                atomic_npz(self.root / (key + '.npz'), **curves)
                atomic_json(self.root / (key + '.json'), {'id': key, 'identity': identity, 'run_id': run_id,
                    'sample_id': run['sample_id'], 'created': time.time(), 'frames': len(curves['source_frame_indices']),
                    'comparison_role': 'declared_GT_error' if is_gt else 'discrepancy_and_motion_review_candidates',
                    'metric_policy': 'Joint/reprojection curves use finite geometry as in aggregate metrics; motion/visible counts also use native presence',
                    'curve_file': file_identity(self.root / (key + '.npz')), 'issues': issues})
        return self.get(key)

    def get(self, key, *, offset=0, limit=50):
        if len(str(key)) != 64 or any(c not in '0123456789abcdef' for c in str(key)):
            raise ValueError('Invalid quality ID')
        metadata = json.loads((self.root / (key + '.json')).read_text())
        if file_identity(self.root / (key + '.npz')) != metadata['curve_file']:
            raise ValueError('Saved quality curves changed')
        identity = metadata['identity']
        for rid, record in ((metadata['run_id'], identity['result']), (identity.get('reference_run'), identity.get('reference'))):
            if rid:
                run = self.manager.run(rid, summary=True)  # validation never starts inference
                if not run['result'] or file_identity(self.manager.root / 'runs' / rid / 'result/result.json') != record:
                    raise ValueError('Diagnostic source changed; regenerate the diagnostics')
        offset, limit = max(0, int(offset)), max(1, min(200, int(limit)))
        issues = metadata.pop('issues')
        with np.load(self.root / (key + '.npz'), allow_pickle=False) as curves:
            # Maxima in time buckets retain spikes; full numeric curves stay downloadable.
            step = max(1, int(np.ceil(metadata['frames'] / 1000)))
            display = []
            for start in range(0, metadata['frames'], step):
                row = {'frame': start, 'source_frame': int(curves['source_frame_indices'][start])}
                for name in ('joint_difference_mm', 'reprojection_difference_px', 'wrist_acceleration_m_s2'):
                    finite = curves[name][start:start + step]
                    finite = finite[np.isfinite(finite)]
                    row[name] = float(finite.max()) if len(finite) else None
                display.append(row)
        return json_values({**metadata, 'issues': issues[offset:offset + limit], 'issue_count': len(issues), 'offset': offset,
                            'curves': display, 'curve_aggregation': 'time-bucket maxima; frames are zero-based sample positions'})
