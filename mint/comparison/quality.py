"""Frame-level diagnostics and review candidates, separate from ground truth."""
from __future__ import annotations

import json
import time

import numpy as np

from mint.runtime import atomic_json, atomic_npz, digest, file_identity
from mint.inference.cache_budget import file_lock
from .contracts import load_result, json_values

QUALITY_REVISION = 'full-sample-timeline-v2'


def _timeline_arrays(arrays, ids, times):
    """Map published rows to sample positions; missing output stays missing."""
    source = np.asarray(arrays['source_frame_indices'])
    positions = np.searchsorted(ids, source)
    matched = positions < len(ids)
    matched[matched] &= ids[positions[matched]] == source[matched]
    if not matched.all():
        raise ValueError('Quality result contains frames outside the input sample timeline')
    if not np.allclose(np.asarray(arrays['timestamps_s']), times[positions], atol=1e-5, rtol=1e-6):
        raise ValueError('Quality result timestamps do not match the input sample timeline')
    n = len(ids)
    result = {'source_frame_indices': ids, 'timestamps_s': times,
              'joints_camera_m': np.full((n, 2, 21, 3), np.nan),
              'geometry_valid': np.zeros((n, 2), bool),
              'K': np.broadcast_to(np.eye(3), (n, 3, 3)).copy(),
              'output_available': np.zeros(n, bool)}
    for key in ('joints_camera_m', 'geometry_valid', 'K'):
        result[key][positions] = arrays[key]
    if 'presence' in arrays:
        result['presence'] = np.zeros((n, 2), bool)
        result['presence'][positions] = arrays['presence']
    result['output_available'][positions] = True
    return result


def _quality_timeline(sample_path, run, arrays, other=None, reference=None):
    if sample_path.is_file():
        sample = json.loads(sample_path.read_text())
        ids = np.asarray(sample['source_frame_indices'])
        times = np.asarray(sample['timestamps_s'], float)
        if ids.ndim != 1 or ids.dtype.kind not in 'iu' or len(ids) != sample['frames'] or not len(ids) or \
                np.any(ids < 0) or np.any(np.diff(ids) <= 0) or times.shape != ids.shape or \
                not np.isfinite(times).all() or times[0] < 0 or np.any(np.diff(times) <= 0):
            raise ValueError('Quality diagnostics require a valid input sample timeline')
        return ids, times, file_identity(sample_path), 'input_sample_timeline'
    # Older completed runs may outlive their RGB cache. A completed run is
    # admitted only with full frame coverage; a partial run cannot establish
    # where absent sample rows should be inserted.
    complete = arrays if run['status'] == 'completed' else reference if other and other['status'] == 'completed' else None
    if complete is None:
        raise ValueError('Partial-result quality diagnostics require the input sample timeline; prepare the input again')
    return np.asarray(complete['source_frame_indices']), np.asarray(complete['timestamps_s']), None, 'completed_result_timeline'


def frame_diagnostics(arrays, reference=None):
    ids = np.asarray(arrays['source_frame_indices'])
    times = np.asarray(arrays['timestamps_s'])
    n = len(ids)
    visible = np.asarray(arrays['geometry_valid']).copy()
    if 'presence' in arrays:
        visible &= arrays['presence']
    output = {'source_frame_indices': ids, 'timestamps_s': times, 'visible_hands': visible.sum(axis=1),
              'geometry_hands': np.asarray(arrays['geometry_valid']).sum(axis=1),
              'output_available': np.asarray(arrays.get('output_available', np.ones(n, bool))),
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
    available = np.asarray(curves.get('output_available', np.ones(len(curves['visible_hands']), bool)))
    geometry = np.asarray(curves.get('geometry_hands', curves['visible_hands']))
    values = [('missing_output_frame', ~available),
              ('no_visible_geometry', available & (geometry == 0)),
              ('low_presence_prediction', available & (geometry > 0) & (curves['visible_hands'] == 0))]
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
        try:
            if isinstance(joint_mm, bool) or isinstance(acceleration, bool) or not (0 < float(joint_mm) <= 10000 and 0 < float(acceleration) <= 100000):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise ValueError('Choose finite positive quality thresholds') from None
        run = self.manager.run(run_id, summary=True)
        reference_run = reference_run or None
        if run.get('result') and run['result'].get('reference_role') == 'user_declared_ground_truth':
            raise ValueError('Choose a prediction as the primary quality result, not a ground-truth reference')
        if reference_run == run_id:
            raise ValueError('Choose distinct prediction and reference results')
        other = self.manager.run(reference_run, summary=True) if reference_run else None
        if not run['result'] or other and not other['result']:
            raise ValueError('Quality diagnostics require published results')
        if other and other['sample_id'] != run['sample_id']:
            raise ValueError('Compare results on the same input timeline')
        path = self.manager.root / 'runs' / run_id / 'result'
        rpath = self.manager.root / 'runs' / reference_run / 'result' if other else None
        sample_path = self.manager.root / 'inputs' / run['sample_id'] / 'sample.json'
        sample_identity = file_identity(sample_path) if sample_path.is_file() else None
        identity = {'schema': 2, 'implementation': QUALITY_REVISION, 'run_id': run_id, 'reference_run': reference_run,
                    'result': file_identity(path / 'result.json'), 'reference': file_identity(rpath / 'result.json') if rpath else None,
                    'sample': sample_identity,
                    'joint_mm': float(joint_mm), 'acceleration': float(acceleration)}
        key = digest(identity)
        with file_lock(self.root / (key + '.lock')):
            if not (self.root / (key + '.json')).exists():
                metadata, arrays = load_result(path)
                reference = load_result(rpath) if rpath else None
                if metadata['sample_id'] != run['sample_id'] or reference and reference[0]['sample_id'] != run['sample_id']:
                    raise ValueError('Quality result metadata belongs to a different input sample')
                ids, times, current_sample_identity, timeline_scope = _quality_timeline(sample_path, run, arrays, other, reference[1] if reference else None)
                if current_sample_identity != sample_identity:
                    raise ValueError('Quality sample timeline changed during diagnostics; regenerate')
                prediction = _timeline_arrays(arrays, ids, times)
                ground = _timeline_arrays(reference[1], ids, times) if reference else None
                curves = frame_diagnostics(prediction, ground)
                is_gt = bool(reference and reference[0].get('reference_role') == 'user_declared_ground_truth')
                issues = issue_intervals(curves, joint_mm=float(joint_mm), acceleration=float(acceleration), reference=is_gt)
                atomic_npz(self.root / (key + '.npz'), **curves)
                atomic_json(self.root / (key + '.json'), {'id': key, 'identity': identity, 'run_id': run_id,
                    'sample_id': run['sample_id'], 'created': time.time(), 'frames': len(curves['source_frame_indices']),
                    'timeline_scope': timeline_scope, 'missing_output_frames': int((~curves['output_available']).sum()),
                    'paired_frames': int((curves['paired_joints'] > 0).sum()),
                    'paired_hand_frames': int(curves['paired_joints'].sum() // 21),
                    'comparison_role': 'declared_GT_error' if is_gt else 'discrepancy_and_motion_review_candidates',
                    'metric_policy': 'Full input timeline; absent output frames remain missing. Joint/reprojection curves use finite geometry; motion/visible counts also use native presence. Presence is not physical visibility or occlusion GT.',
                    'curve_file': file_identity(self.root / (key + '.npz')), 'issues': issues})
        return self.get(key)

    def get(self, key, *, offset=0, limit=50):
        if len(str(key)) != 64 or any(c not in '0123456789abcdef' for c in str(key)):
            raise ValueError('Invalid quality ID')
        metadata = json.loads((self.root / (key + '.json')).read_text())
        if file_identity(self.root / (key + '.npz')) != metadata['curve_file']:
            raise ValueError('Saved quality curves changed')
        identity = metadata['identity']
        if identity.get('sample') and file_identity(identity['sample']['path']) != identity['sample']:
            raise ValueError('Quality sample timeline changed; regenerate the diagnostics')
        sample_path = self.manager.root / 'inputs' / metadata['sample_id'] / 'sample.json'
        if identity.get('schema', 1) >= 2 and not identity.get('sample') and sample_path.is_file():
            raise ValueError('Quality input sample timeline is now available; regenerate the diagnostics')
        for rid, record in ((metadata['run_id'], identity['result']), (identity.get('reference_run'), identity.get('reference'))):
            if rid:
                run = self.manager.run(rid, summary=True)  # validation never starts inference
                if not run['result'] or file_identity(self.manager.root / 'runs' / rid / 'result/result.json') != record:
                    raise ValueError('Diagnostic source changed; regenerate the diagnostics')
        offset, limit = max(0, int(offset)), max(1, min(200, int(limit)))
        issues = metadata.pop('issues')
        with np.load(self.root / (key + '.npz'), allow_pickle=False) as curves:
            source_ids = curves['source_frame_indices']
            if identity.get('schema', 1) < 2:
                if sample_path.is_file():
                    sample = json.loads(sample_path.read_text())
                    ids = np.asarray(sample['source_frame_indices'])
                    times = np.asarray(sample['timestamps_s'])
                    if not np.array_equal(ids, source_ids) or not np.allclose(times, curves['timestamps_s'], atol=1e-5, rtol=1e-6):
                        raise ValueError('Legacy partial-result quality frame mapping is ambiguous; regenerate the diagnostics')
                elif self.manager.run(metadata['run_id'], summary=True)['status'] != 'completed':
                    raise ValueError('Legacy partial-result quality requires regeneration with an input sample timeline')
                metadata.update(timeline_scope='legacy_complete_result_timeline', requires_regeneration=True)
            # Maxima in time buckets retain spikes; full numeric curves stay downloadable.
            step = max(1, int(np.ceil(metadata['frames'] / 1000)))
            display = []
            # NpzFile members are decompressed on every __getitem__. Read
            # each numeric series once, not once per displayed time bucket.
            series = {name: curves[name] for name in ('joint_difference_mm', 'reprojection_difference_px', 'wrist_acceleration_m_s2')}
            for start in range(0, metadata['frames'], step):
                row = {'frame': start, 'end_frame': min(start + step, metadata['frames']),
                       'source_frame': int(source_ids[start]), 'peaks': {}}
                for name, full in series.items():
                    values = full[start:start + step]
                    finite = np.flatnonzero(np.isfinite(values))
                    peak = int(finite[np.argmax(values[finite])]) if len(finite) else None
                    row[name] = float(values[peak]) if peak is not None else None
                    if peak is not None:
                        row['peaks'][name] = {'frame': start + peak, 'source_frame': int(source_ids[start + peak])}
                display.append(row)
        counts = {reason: sum(issue['reason'] == reason for issue in issues) for reason in sorted({issue['reason'] for issue in issues})}
        return json_values({**metadata, 'issues': issues[offset:offset + limit], 'issue_count': len(issues), 'issue_counts': counts, 'offset': offset,
                            'curves': display, 'curve_aggregation': 'time-bucket maxima; frames are zero-based sample positions'})
