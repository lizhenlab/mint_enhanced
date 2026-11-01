"""Sample-aligned review candidates and frozen-reference evaluation integrity."""
import json

import numpy as np
import pytest

from mint.runtime import atomic_json, atomic_npz, digest, file_identity
from mint.comparison.jobs import ComparisonManager
from mint.comparison.quality import QualityService, frame_diagnostics, issue_intervals
from mint.comparison.evaluation import collection_report
from tests.test_comparison import arrays, fake_backend, published, sample
from tests.test_platform_lifecycle import comparison


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    value = ComparisonManager(tmp_path / 'store', autostart=False)
    yield value
    value.close()


def request_collection(comps, selection=None, tracked=False):
    selection = selection or {'algorithm': 'mint'}
    return {'id': 'evaluation', 'status': 'partial', 'selections': [selection], 'options': {},
            'backend_versions': {selection['algorithm'] + ':' + digest(selection): selection['algorithm'] + '-v1'},
            'inputs': [{'source': '/fixture/video.mp4'} for _ in comps],
            'items': [{'status': 'partial', 'comparison_id': comp['id'],
                       **({'run_ids': comp['run_ids'][:]} if tracked else {})} for comp in comps]}


def timeline(manager, comp, n=5):
    path = manager.root / 'inputs' / comp['sample_id'] / 'sample.json'
    atomic_json(path, {**sample(n), 'id': comp['sample_id']})
    return path


def sparse_comparison(manager, tmp_path):
    source = tmp_path / 'sparse.mp4'; source.write_bytes(b'fixture')
    comp = manager.create(str(source), [{'algorithm': 'mint'}, {'algorithm': 'hawor_camera'}])
    pred, gt = comp['run_ids']; values = arrays(world=False)
    sparse = {key: value[[0, 2, 4]] for key, value in values.items()}
    published(manager.root / 'runs' / pred / 'result', sparse, sample_id=comp['sample_id'], run_id=pred, backend_version='mint-v1')
    published(manager.root / 'runs' / gt / 'result', values, sample_id=comp['sample_id'], run_id=gt,
              backend_version='hawor_camera-v1', reference_role='user_declared_ground_truth')
    manager._set(pred, 'partial'); manager._set(gt, 'completed')
    timeline(manager, comp)
    return comp, pred, gt


def test_partial_quality_expands_to_sample_positions_and_flags_omitted_rows(manager, tmp_path):
    comp, pred, gt = sparse_comparison(manager, tmp_path)
    result = QualityService(manager).create(pred, gt)
    assert result['frames'] == 5 and result['missing_output_frames'] == 2
    assert result['timeline_scope'] == 'input_sample_timeline'
    assert result['paired_frames'] == 3 and result['paired_hand_frames'] == 6
    missing = [row for row in result['issues'] if row['reason'] == 'missing_output_frame']
    assert [(row['start'], row['end'], row['source_start']) for row in missing] == [(1, 2, 11), (3, 4, 13)]
    assert 'no_visible_geometry' not in result['issue_counts']
    with np.load(QualityService(manager).root / (result['id'] + '.npz')) as curves:
        np.testing.assert_array_equal(curves['source_frame_indices'], np.arange(10, 15))
        np.testing.assert_array_equal(curves['paired_joints'], [42, 0, 42, 0, 42])
        assert np.isnan(curves['joint_difference_mm'][[1, 3]]).all()


def test_quality_distinguishes_finite_geometry_from_low_predicted_presence():
    values = arrays(world=False); values['presence'] = np.zeros((5, 2), bool)
    values['geometry_valid'][2] = False; values['joints_camera_m'][2] = np.nan
    curves = frame_diagnostics(values)
    issues = issue_intervals(curves)
    low = [row for row in issues if row['reason'] == 'low_presence_prediction']
    missing = [row for row in issues if row['reason'] == 'no_visible_geometry']
    assert [(row['start'], row['end']) for row in low] == [(0, 2), (3, 5)]
    assert [(row['start'], row['end']) for row in missing] == [(2, 3)]
    assert not any(row['reason'] == 'missing_output_frame' for row in issues)


def test_new_quality_identity_tracks_timeline_and_cache_hits_do_not_reload_geometry(manager, tmp_path, monkeypatch):
    comp = comparison(manager, tmp_path); path = timeline(manager, comp)
    service = QualityService(manager); first = service.create(comp['run_ids'][0])
    assert first['identity']['schema'] == 2 and first['identity']['sample'] == file_identity(path)
    monkeypatch.setattr('mint.comparison.quality.load_result', lambda *_: pytest.fail('Cache hit must not read all geometry again'))
    assert service.create(comp['run_ids'][0])['id'] == first['id']
    value = json.loads(path.read_text()); value['fps'] = 29
    atomic_json(path, value)
    with pytest.raises(ValueError, match='sample timeline changed'):
        service.get(first['id'])


def test_quality_timestamp_mismatch_cannot_be_paired_as_same_sample(manager, tmp_path):
    comp = comparison(manager, tmp_path); path = timeline(manager, comp)
    value = json.loads(path.read_text()); value['timestamps_s'] = (np.asarray(value['timestamps_s']) + .2).tolist()
    atomic_json(path, value)
    with pytest.raises(ValueError, match='timestamps'):
        QualityService(manager).create(comp['run_ids'][0])


def test_quality_partial_output_without_known_full_timeline_is_rejected(manager, tmp_path):
    comp, pred, gt = sparse_comparison(manager, tmp_path)
    (manager.root / 'inputs' / comp['sample_id'] / 'sample.json').unlink()
    with pytest.raises(ValueError, match='input sample timeline'):
        QualityService(manager).create(pred)
    # A separately completed reference can still establish the full timeline.
    result = QualityService(manager).create(pred, gt)
    assert result['frames'] == 5 and result['missing_output_frames'] == 2


def test_quality_rejects_gt_as_prediction_and_self_reference(manager, tmp_path):
    comp = comparison(manager, tmp_path, reference=True); pred, gt = comp['run_ids']
    service = QualityService(manager)
    with pytest.raises(ValueError, match='primary quality result'):
        service.create(gt, pred)
    with pytest.raises(ValueError, match='distinct'):
        service.create(pred, pred)


@pytest.mark.parametrize('threshold', [True, False, 0, -1, None, [], float('nan'), float('inf')])
def test_quality_thresholds_do_not_accept_booleans_or_nonfinite_values(manager, tmp_path, threshold):
    comp = comparison(manager, tmp_path)
    with pytest.raises(ValueError, match='finite positive'):
        QualityService(manager).create(comp['run_ids'][0], joint_mm=threshold)


def test_result_only_quality_is_regenerated_when_input_timeline_becomes_available(manager, tmp_path):
    comp = comparison(manager, tmp_path); service = QualityService(manager)
    quality = service.create(comp['run_ids'][0])
    assert quality['timeline_scope'] == 'completed_result_timeline'
    timeline(manager, comp)
    with pytest.raises(ValueError, match='now available'):
        service.get(quality['id'])
    regenerated = service.create(comp['run_ids'][0])
    assert regenerated['id'] != quality['id'] and regenerated['timeline_scope'] == 'input_sample_timeline'


def test_quality_curve_buckets_keep_exact_peak_frame_positions(manager, tmp_path):
    source = tmp_path / 'long.mp4'; source.write_bytes(b'fixture')
    comp = manager.create(str(source), [{'algorithm': 'mint'}, {'algorithm': 'hawor_camera'}])
    pred, gt = comp['run_ids']; truth = arrays(1005, world=False); value = {key: array.copy() for key, array in truth.items()}
    value['joints_camera_m'][1, ..., 0] += 1
    for rid, data in [(pred, value), (gt, truth)]:
        published(manager.root / 'runs' / rid / 'result', data, sample_id=comp['sample_id'], run_id=rid)
        manager._set(rid, 'completed')
    result = QualityService(manager).create(pred, gt)
    first = result['curves'][0]
    assert first['frame'] == 0 and first['end_frame'] == 2
    assert first['joint_difference_mm'] == pytest.approx(1000)
    assert first['peaks']['joint_difference_mm'] == {'frame': 1, 'source_frame': 11}


def test_unsafe_legacy_partial_curve_mapping_requires_regeneration(manager, tmp_path):
    comp, pred, gt = sparse_comparison(manager, tmp_path); service = QualityService(manager)
    quality = service.create(pred, gt); marker = service.root / (quality['id'] + '.json')
    metadata = json.loads(marker.read_text()); metadata['identity']['schema'] = 1; metadata['identity'].pop('sample')
    with np.load(service.root / (quality['id'] + '.npz')) as values:
        shortened = {key: values[key][[0, 2, 4]] for key in values.files}
    atomic_npz(service.root / (quality['id'] + '.npz'), **shortened)
    metadata['frames'] = 3; metadata['curve_file'] = file_identity(service.root / (quality['id'] + '.npz'))
    atomic_json(marker, metadata)
    with pytest.raises(ValueError, match='Legacy partial-result'):
        service.get(quality['id'])


def test_evaluation_frozen_run_ids_ignore_later_reference_attachments(manager, tmp_path):
    comp = comparison(manager, tmp_path, reference=True); collection = request_collection([comp], tracked=True)
    source = comp['sample_request']['source']['path']
    extra = manager.create(source, [{'algorithm': 'ace_kfree'}])
    rid = extra['run_ids'][0]
    published(manager.root / 'runs' / rid / 'result', sample_id=comp['sample_id'], run_id=rid,
              reference_role='user_declared_ground_truth', backend_version='ace_kfree-v1')
    manager._set(rid, 'completed'); manager.attach(comp['id'], rid)
    report = collection_report(manager, collection)
    assert report['rows'][0]['scored_sequences'] == 1 and report['scorable_sequences'] == 1
    assert report['rows'][0]['camera_absolute_joint_mm'] == 0


def test_ground_truth_with_native_algorithm_name_is_never_scored_against_itself(manager, tmp_path):
    comp = comparison(manager, tmp_path, reference=True)
    report = collection_report(manager, request_collection([comp], {'algorithm': 'hawor_camera'}))
    row = report['rows'][0]
    assert row['scored_sequences'] == 0 and row['failed_sequences'] == 1
    assert row['camera_absolute_joint_mm'] is None and row['gt_geometry_coverage'] == 0


def test_one_missing_child_does_not_abort_other_evaluation_sequences(manager, tmp_path):
    comp = comparison(manager, tmp_path, reference=True)
    collection = request_collection([comp, {'id': '0' * 64}])
    report = collection_report(manager, collection); row = report['rows'][0]
    assert row['attempted_sequences'] == 2 and row['scored_sequences'] == 1
    assert row['failed_sequences'] == row['unscorable_sequences'] == 1
    assert row['gt_geometry_coverage'] is None
    assert row['available_reference_geometry_coverage'] == 1


def test_missing_execution_rows_remain_in_frozen_attempt_counts(manager, tmp_path):
    comp = comparison(manager, tmp_path, reference=True)
    collection = request_collection([comp]); collection['inputs'].append({'source': '/fixture/never-created.mp4'})
    row = collection_report(manager, collection)['rows'][0]
    assert row['attempted_sequences'] == 2 and row['unscorable_sequences'] == 1


def test_empty_reference_is_unscorable_instead_of_a_perfect_prediction(manager, tmp_path):
    source = tmp_path / 'empty.mp4'; source.write_bytes(b'fixture')
    comp = manager.create(str(source), [{'algorithm': 'mint'}, {'algorithm': 'hawor_camera'}])
    pred, gt = comp['run_ids']; truth = arrays(world=False)
    truth['geometry_valid'][:] = False; truth['joints_camera_m'][:] = np.nan
    for rid, data in [(pred, arrays(world=False)), (gt, truth)]:
        published(manager.root / 'runs' / rid / 'result', data, sample_id=comp['sample_id'], run_id=rid,
                  backend_version='mint-v1' if rid == pred else 'hawor_camera-v1',
                  **({'reference_role': 'user_declared_ground_truth'} if rid == gt else {}))
        manager._set(rid, 'completed')
    report = collection_report(manager, request_collection([comp])); row = report['rows'][0]
    assert report['scorable_sequences'] == 0 and row['scored_sequences'] == 0
    assert row['unscorable_sequences'] == 1 and row['camera_absolute_joint_mm'] is None


def test_published_partial_output_is_counted_explicitly(manager, tmp_path):
    comp, pred, gt = sparse_comparison(manager, tmp_path)
    row = collection_report(manager, request_collection([comp], tracked=True))['rows'][0]
    assert row['scored_sequences'] == 1 and row['partial_sequences'] == 1
    assert row['completed_sequences'] == 0 and row['failed_sequences'] == 0
    assert row['gt_geometry_coverage'] == .6


@pytest.mark.parametrize('body', [{}, {'run_id': 'prediction', 'unknown': True}, {'run_id': False}, {'run_id': 'prediction', 'reference_run': True}])
def test_quality_api_rejects_unknown_settings_before_opening_services(monkeypatch, body):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    monkeypatch.setattr('mint.comparison.api.ComparisonManager', lambda: pytest.fail('Invalid quality request cannot initialize services'))
    app = Flask(__name__); app.register_blueprint(comparison_blueprint())
    response = app.test_client().post('/api/comparison/quality', json=body)
    assert response.status_code == 400


def test_breakdown_api_does_not_treat_reference_as_prediction(manager, tmp_path):
    from mint.comparison.diagnostics import run_breakdown
    comp = comparison(manager, tmp_path, reference=True)
    with pytest.raises(ValueError, match='primary breakdown result'):
        run_breakdown(manager, comp['run_ids'][1], comp['run_ids'][0])


def test_breakdown_sparse_predictions_do_not_shrink_reference_denominator():
    from mint.comparison.diagnostics import error_breakdown
    reference = arrays(world=False)
    sparse = {key: value[[0, 2, 4]] for key, value in reference.items()}
    result = error_breakdown(sparse, reference, image_hw=(480, 640))
    assert result['aligned_frames'] == 3 and result['reference_frames'] == 5
    overall = result['groups']['all']
    assert overall['gt_hand_frames'] == 10 and overall['paired_hand_frames'] == 6
    assert overall['geometry_coverage'] == .6
    assert overall['detection_recall_on_labelled_positive_hands'] == .6
    assert overall['absolute_joint_mm']['mean'] == 0
    assert result['groups']['left']['geometry_coverage'] == .6
    assert result['groups']['depth_ge_100cm']['gt_hand_frames'] == 10


def test_breakdown_rejects_matching_source_ids_with_conflicting_timestamps():
    from mint.comparison.diagnostics import error_breakdown
    reference = arrays(world=False)
    prediction = {key: value.copy() for key, value in reference.items()}
    prediction['timestamps_s'] += .1
    with pytest.raises(ValueError, match='timestamps'):
        error_breakdown(prediction, reference)


def test_quality_plot_reads_each_compressed_series_once(manager, tmp_path, monkeypatch):
    comp = comparison(manager, tmp_path); service = QualityService(manager)
    result = service.create(comp['run_ids'][0]); original = np.load; reads = {}
    class CountedArchive:
        def __init__(self, *args, **kwargs): self.archive = original(*args, **kwargs)
        def __enter__(self): return self
        def __exit__(self, *_): self.archive.close()
        def __getitem__(self, key):
            reads[key] = reads.get(key, 0) + 1
            return self.archive[key]
    monkeypatch.setattr('mint.comparison.quality.np.load', CountedArchive)
    assert service.get(result['id'])['frames'] == 5
    assert reads == {'source_frame_indices': 1, 'joint_difference_mm': 1,
                     'reprojection_difference_px': 1, 'wrist_acceleration_m_s2': 1}


def test_unrelated_missing_attachment_cannot_break_a_frozen_evaluation(manager, tmp_path):
    comp = comparison(manager, tmp_path, reference=True); collection = request_collection([comp], tracked=True)
    with manager.db() as db:
        value = json.loads(db.execute('SELECT payload FROM comparisons WHERE id=?', (comp['id'],)).fetchone()['payload'])
        value['run_ids'].append('0' * 64)
        db.execute('UPDATE comparisons SET payload=? WHERE id=?', (json.dumps(value), comp['id']))
    row = collection_report(manager, collection)['rows'][0]
    assert row['scored_sequences'] == 1 and row['failed_sequences'] == 0 and row['gt_geometry_coverage'] == 1


def test_one_corrupt_prediction_keeps_available_gt_in_the_coverage_denominator(manager, tmp_path):
    first = comparison(manager, tmp_path, reference=True)
    second = comparison(manager, tmp_path, reference=True, start=1)
    path = manager.root / 'runs' / second['run_ids'][0] / 'result/joints_camera_m.npy'
    path.write_bytes(b'corrupt prediction')
    row = collection_report(manager, request_collection([first, second], tracked=True))['rows'][0]
    assert row['scored_sequences'] == row['failed_sequences'] == 1
    assert row['unscorable_sequences'] == 0 and row['gt_hand_slots'] == 20
    assert row['gt_geometry_coverage'] == .5


def test_paired_metrics_rejects_conflicting_timestamps_instead_of_scoring(tmp_path):
    from mint.comparison.results import paired_metrics
    reference = arrays(world=False)
    prediction = {key: value.copy() for key, value in reference.items()}
    prediction['timestamps_s'] += .1
    published(tmp_path / 'prediction', prediction)
    published(tmp_path / 'reference', reference, reference_role='user_declared_ground_truth')
    with pytest.raises(ValueError, match='timestamps'):
        paired_metrics(tmp_path / 'prediction', tmp_path / 'reference')


def test_paired_metrics_accepts_typical_float32_timestamp_roundoff(tmp_path):
    from mint.comparison.results import paired_metrics
    reference = arrays(world=False)
    prediction = {key: value.copy() for key, value in reference.items()}
    prediction['timestamps_s'] = prediction['timestamps_s'].astype(np.float32)
    published(tmp_path / 'prediction', prediction)
    published(tmp_path / 'reference', reference, reference_role='user_declared_ground_truth')
    metrics = paired_metrics(tmp_path / 'prediction', tmp_path / 'reference')
    assert metrics['common_time_frames'] == 5 and metrics['paired_joints'] == 210
    assert metrics['camera_absolute_joint_mm'] == 0
