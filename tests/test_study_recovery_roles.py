"""CPU-only recovery of frozen prediction variants after reference attachment."""
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np

from mint.comparison.contracts import publish_result
from mint.comparison.jobs import ComparisonManager
from mint.comparison.research import ResearchRegistry
from mint.comparison.studies import StudyManager
from mint.runtime import file_identity


def _backend(name, **selection):
    return {'id': name, 'version': name + '-v1', 'ready': True, 'camera_required': False,
            'label': name, 'identity': {}, 'root': '/unused', 'python': '/unused', 'missing': [], **selection}


def _publish(manager, run_id, *, role=None):
    run = manager.run(run_id, summary=True)
    n = 3
    values = {'joints_camera_m': np.ones((n, 2, 21, 3), np.float32),
              'geometry_valid': np.ones((n, 2), bool),
              'source_frame_indices': np.arange(n), 'timestamps_s': np.arange(n) / 30,
              'K': np.broadcast_to([[500., 0, 320], [0, 500, 240], [0, 0, 1]], (n, 3, 3)).copy()}
    publish_result(manager.root / 'runs' / run_id / 'result', values,
                   {'sample_id': run['sample_id'], 'run_id': run_id, 'label': run['label'],
                    **({'reference_role': role} if role else {})})
    manager._set(run_id, 'completed')


@contextmanager
def _platform(tmp_path):
    with patch('mint.comparison.jobs.backend_spec', _backend), \
            patch('mint.comparison.studies.backend_spec', _backend), \
            patch('mint.comparison.research.PROJECT', tmp_path):
        manager = ComparisonManager(tmp_path / 'store', autostart=False)
        registry = ResearchRegistry(manager)
        studies = StudyManager(manager, registry, None, autostart=False)
        try:
            yield manager, registry, studies
        finally:
            studies.close()
            manager.close()


def _protocol(manager, registry, tmp_path, count=1):
    comparisons = []
    for index in range(count):
        source = tmp_path / f'input-{index}.mp4'
        source.write_bytes(b'CPU fixture, never decoded')
        comp = manager.create(source, [{'algorithm': 'mint'}, {'algorithm': 'hawor_camera'}])
        _publish(manager, comp['run_ids'][0])
        _publish(manager, comp['run_ids'][1], role='user_declared_ground_truth')
        comparisons.append(comp)
    return registry.protocol(name='frozen test inputs', comparisons=[comp['id'] for comp in comparisons])


def _unrecorded_child(manager, job, index, *, status='completed', role=None):
    case = job['request']['cases'][index]
    sample = case['sample_request']
    tag = f'{index}:None:0'
    comp = manager.create(sample['source']['path'], job['request']['selections'],
                          **{key: sample[key] for key in ('view', 'target_fps', 'max_frames', 'start_frame', 'camera')},
                          comparison_key='study:' + job['id'] + ':' + tag,
                          execution={'priority': 'background', 'cache': 'exact'})
    prediction = comp['run_ids'][0]
    if status == 'completed':
        _publish(manager, prediction, role=role)
    else:
        manager._set(prediction, status, error='preserved failed prediction')
    manager.attach(comp['id'], case['reference_run'])
    return tag, comp, prediction


def test_recovered_regression_scores_only_frozen_predictions_and_keeps_attached_history(tmp_path):
    with _platform(tmp_path) as (manager, registry, studies):
        protocol = _protocol(manager, registry, tmp_path)
        job = studies.create(kind='regression', protocol_id=protocol['id'],
                             selections=[{'algorithm': 'mint', 'options': {'hand_mode': 'hard'}}])
        tag, comp, prediction = _unrecorded_child(manager, job, 0, role='derived_camera_combination')
        reference = protocol['entries'][0]['reference_run']
        # A separately attached prediction also remains part of the comparison,
        # but was never one of this study's frozen selections.
        original = manager.comparison(protocol['entries'][0]['comparison_id'])['run_ids'][0]
        manager.attach(comp['id'], original)
        before = {run: file_identity(manager.root / 'runs' / run / 'result/result.json')
                  for run in (prediction, reference, original)}
        studies._set(job['id'], status='running', owner_pid=999999999, owner_token='dead')
        recovered = StudyManager(manager, registry, None, autostart=False)
        try:
            child = recovered.get(job['id'])['children'][tag]
            assert child['runs'] == [prediction]
            assert child['owned'] == [prediction]
            assert manager.comparison(comp['id'])['run_ids'] == [prediction, reference, original]
            recovered.resume(job['id'])
            with patch.object(manager, 'create', side_effect=AssertionError('Recovered children must be reused')):
                recovered._execute(job['id'])
            result = recovered.get(job['id'])
            assert result['status'] == 'completed'
            report = registry.get(result['evaluation_id'], 'evaluation')
            assert report['rows'][0]['inputs'][0]['run_id'] == prediction
            assert report['rows'][0]['total_sequences'] == report['rows'][0]['scored_sequences'] == 1
            assert {run: file_identity(record['path']) for run, record in before.items()} == before
        finally:
            recovered.close()


def test_recovery_retains_failed_predictions_in_coverage_and_preserves_study_history(tmp_path):
    with _platform(tmp_path) as (manager, registry, studies):
        protocol = _protocol(manager, registry, tmp_path, count=2)
        job = studies.create(kind='regression', protocol_id=protocol['id'],
                             selections=[{'algorithm': 'mint', 'options': {'hand_mode': 'hard'}}])
        first_tag, _, first = _unrecorded_child(manager, job, 0)
        failed_tag, _, failed = _unrecorded_child(manager, job, 1, status='failed')
        old_rows = [{'history': 'retain the previous coordinator observation'}]
        studies._set(job['id'], status='running', owner_pid=999999999, owner_token='dead', rows=old_rows)
        recovered = StudyManager(manager, registry, None, autostart=False)
        try:
            state = recovered.get(job['id'])
            assert state['rows'] == old_rows
            assert state['children'][first_tag]['runs'] == [first]
            assert state['children'][failed_tag]['runs'] == [failed]
            assert state['children'][failed_tag]['owned'] == [failed]
            assert manager.run(failed)['status'] == 'failed'
            assert manager.run(failed)['error'] == 'preserved failed prediction'
            # Run the recovered coordinator over terminal children without
            # requesting a retry or launching a worker for the failed input.
            recovered._set(job['id'], status='queued')
            with patch.object(manager, 'create', side_effect=AssertionError('Recovery must not rerun children')):
                recovered._execute(job['id'])
            state = recovered.get(job['id'])
            report = registry.get(state['evaluation_id'], 'evaluation')
            row = report['rows'][0]
            assert state['status'] == 'partial'
            assert row['total_sequences'] == 2 and row['scored_sequences'] == row['failed_sequences'] == 1
            assert row['gt_geometry_coverage'] == .5
            assert {entry['run_id'] for entry in row['inputs']} == {first, failed}
            assert manager.run(failed)['error'] == 'preserved failed prediction'
        finally:
            recovered.close()


def test_existing_polluted_child_is_repaired_on_recovery(tmp_path):
    with _platform(tmp_path) as (manager, registry, studies):
        protocol = _protocol(manager, registry, tmp_path)
        job = studies.create(kind='regression', protocol_id=protocol['id'],
                             selections=[{'algorithm': 'mint', 'options': {'hand_mode': 'hard'}}])
        tag, comp, prediction = _unrecorded_child(manager, job, 0)
        reference = protocol['entries'][0]['reference_run']
        polluted = {'comparison_id': comp['id'], 'runs': [prediction, reference],
                    'owned': [prediction], 'started': comp['created'], 'resumed_attempts': 2}
        studies._set(job['id'], status='running', owner_pid=999999999, owner_token='dead', children={tag: polluted})
        recovered = StudyManager(manager, registry, None, autostart=False)
        try:
            child = recovered.get(job['id'])['children'][tag]
            assert child['runs'] == [prediction] and child['owned'] == [prediction]
            assert child['resumed_attempts'] == 2 and child['started'] == comp['created']
            assert manager.comparison(comp['id'])['run_ids'] == [prediction, reference]
        finally:
            recovered.close()


def test_performance_recovery_matches_gpu_cap_and_its_original_fresh_attempt(tmp_path):
    with _platform(tmp_path) as (manager, registry, studies):
        protocol = _protocol(manager, registry, tmp_path)
        case = protocol['entries'][0]
        job = studies.create(kind='performance', comparison_id=case['comparison_id'],
                             selections=[{'algorithm': 'mint', 'options': {'hand_mode': 'hard'}}], gpu_counts=[2])
        tag = '0:2:0'
        sample = job['request']['cases'][0]['sample_request']
        selections = [{'algorithm': 'mint', 'options': {'hand_mode': 'hard', 'max_gpus': 2}}]
        comp = manager.create(sample['source']['path'], selections,
                              **{key: sample[key] for key in ('view', 'target_fps', 'max_frames', 'start_frame', 'camera')},
                              comparison_key='study:' + job['id'] + ':' + tag,
                              execution={'priority': 'background', 'cache': 'fresh'})
        prediction = comp['run_ids'][0]
        _publish(manager, prediction)
        unrelated = manager.create(sample['source']['path'], selections, execution={'cache': 'fresh'})
        other = unrelated['run_ids'][0]
        _publish(manager, other)
        manager.attach(comp['id'], other)
        manager.attach(comp['id'], case['reference_run'])
        studies._set(job['id'], status='running', owner_pid=999999999, owner_token='dead')
        recovered = StudyManager(manager, registry, None, autostart=False)
        try:
            child = recovered.get(job['id'])['children'][tag]
            assert child['runs'] == child['owned'] == [prediction]
            assert manager.comparison(comp['id'])['run_ids'] == [prediction, other, case['reference_run']]
        finally:
            recovered.close()
