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


def collection_report(manager, collection):
    models, sequences = {}, []
    expected = [(selection['algorithm'], normalize_options(selection['algorithm'], selection.get('options')),
                 collection['backend_versions'][selection['algorithm'] + ':' + digest(selection)])
                for selection in collection['selections']]
    # Start from the frozen submission, not just successfully-created child
    # runs: a failed child creation must still appear in the model's attempts.
    for algorithm, options, version in expected:
        key = digest({'algorithm': algorithm, 'options': options, 'backend': version})
        models.setdefault(key, {'id': key, 'algorithm': algorithm, 'label': ALGORITHMS[algorithm]['label'],
            'options': options, 'attempted_sequences': 0, 'scored_sequences': 0,
            'failed_sequences': 0, 'unscorable_sequences': 0, 'gt_hand_slots': 0,
            'paired_hand_slots': 0, 'records': []})
    for index, item in enumerate(collection['items']):
        descriptor = collection['inputs'][index]
        sequence = {'index': index, 'source': descriptor['source'], 'name': descriptor.get('name'),
                    'comparison_id': item.get('comparison_id'), 'status': item['status'], 'metrics': []}
        for record in models.values():
            record['attempted_sequences'] += 1
        if not item.get('comparison_id'):
            sequence['error'] = item.get('error', 'No comparison was created')
            for record in models.values():
                record['failed_sequences'] += 1
                record['unscorable_sequences'] += 1
            sequences.append(sequence)
            continue
        comparison = manager.comparison(item['comparison_id'])
        references = [run for run in comparison['runs'] if run['result'] and
                      run['result'].get('reference_role') == 'user_declared_ground_truth']
        if len(references) != 1:
            sequence['error'] = 'Evaluation needs exactly one validated declared reference'
            for record in models.values():
                record['failed_sequences'] += 1
                record['unscorable_sequences'] += 1
            sequences.append(sequence)
            continue
        reference = references[0]
        reference_dir = manager.root / 'runs' / reference['id'] / 'result'
        reference_meta, arrays = load_result(reference_dir)
        gt_slots = int(arrays['geometry_valid'].sum())
        sequence.update(reference_run=reference['id'], gt_hand_slots=gt_slots)
        for record in models.values():
            record['gt_hand_slots'] += gt_slots
        seen = set()
        for run in comparison['runs']:
            if run['algorithm'] == 'reference' or run['result'] and run['result'].get('component_sources'):
                continue
            backend = (run['result'] or {}).get('backend_version') or json.loads(manager.row(run['id'])['payload'])['backend_version']
            if run.get('imported') or not any(run['algorithm'] == algorithm and run['options'] == options and backend == version for algorithm, options, version in expected):
                continue
            key = digest({'algorithm': run['algorithm'], 'options': run['options'],
                          'backend': backend})
            if key in seen:
                continue
            seen.add(key)
            record = models[key]
            record['label'] = run['label']
            if run['result']:
                row = paired_metrics(manager.root / 'runs' / run['id'] / 'result', reference_dir)
                row.update(sequence=index, algorithm=run['algorithm'])
                record['records'].append(row)
                record['scored_sequences'] += 1
                record['paired_hand_slots'] += row['paired_hand_slots']
                sequence['metrics'].append(row)
            else:
                record['failed_sequences'] += 1
                sequence['metrics'].append({'algorithm': run['algorithm'], 'run_id': run['id'], 'error': run.get('error') or run['status']})
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
    return json_values({'schema': 1, 'collection_id': collection['id'], 'status': collection['status'],
        'protocol': {'name': 'same-input-openpose21-v1', 'source': 'Explicit LeRobot or user-declared normalized reference',
                     'input_options': collection['options'], 'selections': collection['selections'],
                     'backend_versions': collection['backend_versions'],
                     'hand_aggregation': 'Joint/hand count-weighted means on finite intersections; coverage includes failed predictions; overall coverage unavailable when any reference is unscorable',
                     'camera_aggregation': 'Each sequence aligned separately; RMSE combined using valid camera/relative-pose counts',
                     'information': 'Dataset annotations are reference-only unless explicit calibration is enabled',
                     'scope': 'Dataset evaluation under this declared protocol, not an automatic reproduction of public paper leaderboard protocols'},
        'total_sequences': len(collection['inputs']), 'scorable_sequences': sum('error' not in row for row in sequences),
        'rows': rows, 'sequences': sequences})


def report_csv(report):
    stream = io.StringIO()
    rows = report.get('rows', [])
    fields = ['label', 'algorithm', 'attempted_sequences', 'scored_sequences', 'failed_sequences', 'unscorable_sequences',
              'gt_hand_slots', 'paired_hand_slots', 'gt_geometry_coverage', 'available_reference_geometry_coverage', *MEANS, *RMS]
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        safe = {key: ("'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value)
                for key, value in row.items()}
        writer.writerow(safe)
    return '\ufeff' + stream.getvalue()
