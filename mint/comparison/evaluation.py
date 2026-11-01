"""Fixed-input multi-backend evaluation with explicit GT and count-weighted aggregation."""
from __future__ import annotations

import csv
import io
import math
import json

from mint.runtime import digest
from .contracts import load_result, json_values
from .results import paired_metrics
from .registry import ALGORITHMS, normalize_options

MEANS = {
    'camera_absolute_joint_mm': 'paired_joints',
    'wrist_aligned_joint_mm': 'paired_joints',
    'similarity_aligned_joint_mm': 'paired_joints',
    'camera_wrist_mm': 'paired_hand_slots',
    'reprojection_difference_px': 'paired_projectable_joints',
    'camera_wrist_velocity_difference_m_s': 'paired_velocity_samples',
}
RMS = {
    'first_camera_translation_rmse_m': 'world_camera_common_frames',
    'camera_ate_se3_rmse_m': 'world_camera_common_frames',
    'camera_ate_sim3_rmse_m': 'world_camera_common_frames',
    'camera_rpe_translation_rmse_m': 'camera_rpe_samples',
    'camera_rpe_rotation_rmse_deg': 'camera_rpe_samples',
}


def aggregate_pairs(records):
    output = {}
    for name, weight_name in {**MEANS, **RMS}.items():
        pairs = [(row.get(name), row.get(weight_name, 0)) for row in records]
        pairs = [(value, weight) for value, weight in pairs
                 if isinstance(value, (int, float)) and math.isfinite(value) and weight > 0]
        weight = sum(n for _, n in pairs)
        value = sum((v * v if name in RMS else v) * n for v, n in pairs) / weight if weight else None
        output[name] = math.sqrt(value) if value is not None and name in RMS else value
        output[name + '_samples'] = weight
    return output


def _comparison_snapshot(manager, comparison_id, tracked=None):
    """Read only frozen run metadata, without loading unrelated geometry."""
    with manager.db() as db:
        row = db.execute('SELECT payload FROM comparisons WHERE id=?', (comparison_id,)).fetchone()
    if row is None:
        raise KeyError('Comparison not found')
    comparison = json.loads(row['payload'])
    allowed = set(tracked) if tracked is not None else None
    runs = []
    for run_id in comparison['run_ids']:
        if allowed is not None and run_id not in allowed:
            continue
        try:
            runs.append(manager.run(run_id, summary=True))
        except (KeyError, OSError, ValueError):
            # A missing requested prediction is counted below; an unrelated
            # attachment must not invalidate a frozen batch or its GT.
            continue
    return {**comparison, 'runs': runs}


def collection_report(manager, collection):
    models, sequences = {}, []
    expected = [(selection['algorithm'], normalize_options(selection['algorithm'], selection.get('options')),
                 collection['backend_versions'][selection['algorithm'] + ':' + digest(selection)])
                for selection in collection['selections']]
    for algorithm, options, version in expected:
        key = digest({'algorithm': algorithm, 'options': options, 'backend': version})
        models.setdefault(key, {'id': key, 'algorithm': algorithm, 'label': ALGORITHMS[algorithm]['label'],
            'options': options, 'attempted_sequences': 0, 'scored_sequences': 0,
            'completed_sequences': 0, 'partial_sequences': 0,
            'failed_sequences': 0, 'unscorable_sequences': 0, 'gt_hand_slots': 0,
            'paired_hand_slots': 0, 'records': []})
    # The denominator follows the submitted inputs, including absent execution
    # rows. Later manual attachments are outside this batch's frozen protocol.
    for index, descriptor in enumerate(collection['inputs']):
        item = collection['items'][index] if index < len(collection['items']) else {'status': 'failed', 'error': 'No child execution record'}
        sequence = {'index': index, 'source': descriptor.get('source'), 'name': descriptor.get('name'),
                    'comparison_id': item.get('comparison_id'), 'status': item['status'], 'metrics': []}
        for record in models.values():
            record['attempted_sequences'] += 1
        try:
            if not item.get('comparison_id'):
                raise ValueError(item.get('error', 'No comparison was created'))
            comparison = _comparison_snapshot(manager, item['comparison_id'], item.get('run_ids'))
            runs = comparison['runs']
            references = [run for run in runs if run['result'] and
                          run['result'].get('reference_role') == 'user_declared_ground_truth']
            if len(references) != 1:
                raise ValueError('Evaluation needs exactly one validated declared reference in the frozen batch')
            reference = references[0]
            reference_dir = manager.root / 'runs' / reference['id'] / 'result'
            reference_meta, arrays = load_result(reference_dir)
            if reference_meta['sample_id'] != comparison['sample_id']:
                raise ValueError('Reference belongs to a different input sample')
            gt_slots = int(arrays['geometry_valid'].sum())
            poses = arrays.get('pose_valid')
            if not gt_slots and (poses is None or not poses.any()):
                raise ValueError('Reference contains no valid hand geometry or camera poses')
        except (KeyError, OSError, ValueError) as error:
            sequence['error'] = str(error)
            for record in models.values():
                record['failed_sequences'] += 1
                record['unscorable_sequences'] += 1
            sequences.append(sequence)
            continue
        sequence.update(reference_run=reference['id'], gt_hand_slots=gt_slots)
        for record in models.values():
            record['gt_hand_slots'] += gt_slots
        seen = set()
        for run in runs:
            metadata = run['result'] or {}
            if run['id'] == reference['id'] or run['algorithm'] == 'reference' or \
                    metadata.get('reference_role') == 'user_declared_ground_truth' or metadata.get('component_sources'):
                continue
            if run.get('imported'):
                continue
            # A bad/missing result is one failed sequence, not a reason to
            # discard other inputs or a valid reference denominator.
            try:
                backend = metadata.get('backend_version') or json.loads(manager.row(run['id'])['payload'])['backend_version']
            except (KeyError, OSError, ValueError):
                continue
            if not any(run['algorithm'] == algorithm and run['options'] == options and backend == version
                       for algorithm, options, version in expected):
                continue
            key = digest({'algorithm': run['algorithm'], 'options': run['options'], 'backend': backend})
            if key in seen:
                continue
            seen.add(key); record = models[key]; record['label'] = run['label']
            if not run['result']:
                record['failed_sequences'] += 1
                sequence['metrics'].append({'algorithm': run['algorithm'], 'run_id': run['id'], 'error': run.get('error') or run['status']})
                continue
            try:
                row = paired_metrics(manager.root / 'runs' / run['id'] / 'result', reference_dir)
            except (KeyError, OSError, ValueError) as error:
                record['failed_sequences'] += 1
                sequence['metrics'].append({'algorithm': run['algorithm'], 'run_id': run['id'], 'error': str(error)})
                continue
            row.update(sequence=index, algorithm=run['algorithm'], prediction_status=run['status'])
            record['records'].append(row)
            record['scored_sequences'] += 1
            record['completed_sequences' if run['status'] == 'completed' else 'partial_sequences'] += 1
            record['paired_hand_slots'] += row['paired_hand_slots']
            sequence['metrics'].append(row)
        for key, record in models.items():
            if key not in seen:
                record['failed_sequences'] += 1
                sequence['metrics'].append({'algorithm': record['algorithm'], 'model_id': key,
                                            'error': 'No result matches the frozen backend and numerical protocol'})
        sequences.append(sequence)
    rows = []
    for model in models.values():
        records = model.pop('records')
        coverage = model['paired_hand_slots'] / model['gt_hand_slots'] if model['gt_hand_slots'] else None
        rows.append({**model, **aggregate_pairs(records),
                     'gt_geometry_coverage': coverage if not model['unscorable_sequences'] else None,
                     'available_reference_geometry_coverage': coverage})
    return json_values({'schema': 2, 'collection_id': collection['id'], 'status': collection['status'],
        'protocol': {'name': 'same-input-openpose21-v1', 'source': 'Explicit LeRobot or user-declared normalized reference',
                     'input_options': collection['options'], 'selections': collection['selections'],
                     'backend_versions': collection['backend_versions'],
                     'hand_aggregation': 'Joint/hand count-weighted means on finite intersections; coverage includes failed predictions; overall coverage unavailable when any reference is unscorable',
                     'camera_aggregation': 'Each sequence aligned separately; RMSE combined using valid camera/relative-pose counts',
                     'sequence_counts': 'Scored sequences have published metric calculations; completed and partial are disjoint subsets. Failed sequences include unscorable references; these are not inference-process failure counts.',
                     'information': 'Dataset annotations are reference-only unless explicit calibration is enabled',
                     'scope': 'Dataset evaluation under this declared protocol, not an automatic reproduction of public paper leaderboard protocols'},
        'total_sequences': len(collection['inputs']), 'scorable_sequences': sum('error' not in row for row in sequences),
        'rows': rows, 'sequences': sequences})


def report_csv(report):
    stream = io.StringIO()
    rows = report.get('rows', [])
    fields = ['label', 'algorithm', 'attempted_sequences', 'scored_sequences', 'completed_sequences', 'partial_sequences', 'failed_sequences', 'unscorable_sequences',
              'gt_hand_slots', 'paired_hand_slots', 'gt_geometry_coverage', 'available_reference_geometry_coverage', *MEANS, *RMS]
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        safe = {key: ("'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value)
                for key, value in row.items()}
        writer.writerow(safe)
    return '\ufeff' + stream.getvalue()
