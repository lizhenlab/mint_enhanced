"""Geometry, identity, time, cache and task isolation across native algorithms."""
import concurrent.futures
import json
import threading
import time
import zipfile
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from mint.comparison.contracts import (source_request, validate_geometry, publish_result,
    load_result, transform_points, json_values)
from mint.comparison.jobs import ComparisonManager
from mint.comparison.results import (frame_payload, common_world_origin, combine_camera,
    paired_metrics, trajectory)


def sample(n=5):
    return {'id': 'same-input', 'frames': n, 'fps': 30, 'width': 640, 'height': 480,
            'source_frame_indices': list(range(10, 10 + n)), 'timestamps_s': (np.arange(n) / 30 + 2).tolist()}


def arrays(n=5, world=True):
    value = {'joints_camera_m': np.ones((n, 2, 21, 3), np.float32),
             'geometry_valid': np.ones((n, 2), bool),
             'source_frame_indices': np.arange(10, 10 + n), 'timestamps_s': np.arange(n) / 30 + 2,
             'K': np.broadcast_to([[500., 0, 320], [0, 500, 240], [0, 0, 1]], (n, 3, 3)).copy()}
    if world:
        T = np.broadcast_to(np.eye(4), (n, 4, 4)).copy(); T[:, 0, 3] = np.arange(n) * .1 + 5
        value.update(T_world_camera=T, pose_valid=np.ones(n, bool))
    return value


def published(path, value=None, **metadata):
    return publish_result(path, arrays() if value is None else value,
        {'sample_id': 'same-input', 'run_id': path.name, 'label': path.name, **metadata})


def test_source_identity_and_explicit_full_timeline(tmp_path):
    path = tmp_path / 'video.mp4'; path.write_bytes(b'original')
    first = source_request(path, view='left')
    assert first['max_frames'] is None and first['target_fps'] == 0
    assert first['id'] != source_request(path, view='right')['id']
    assert first['id'] != source_request(path, target_fps=30)['id']
    path.write_bytes(b'replaced at same name')
    assert source_request(path, view='left')['id'] != first['id']


@pytest.mark.parametrize('kwargs', [{'target_fps': float('nan')}, {'max_frames': 0}, {'start_frame': -1},
    {'max_frames': 3.5}, {'view': 'unknown'}, {'camera': {'K': np.eye(3).tolist(), 'width': 640, 'height': 480}}])
def test_input_request_rejects_ambiguous_or_invalid_options(tmp_path, kwargs):
    path = tmp_path / 'v.mp4'; path.write_bytes(b'v')
    with pytest.raises(ValueError): source_request(path, **kwargs)


def test_geometry_requires_explicit_missing_values_and_real_rigid_poses():
    value = arrays()
    value['geometry_valid'][0, 0] = False
    with pytest.raises(ValueError, match='missing'): validate_geometry(value)
    value['joints_camera_m'][0, 0] = np.nan
    assert validate_geometry(value) == 5
    value['T_world_camera'][0, 0, 0] = -1
    with pytest.raises(ValueError, match='rigid'): validate_geometry(value)


def test_unavailable_confidence_is_not_synthesized_and_probabilities_are_checked():
    value = arrays(world=False)
    assert validate_geometry(value) == 5
    value['confidence'] = np.full((5, 2), 2.)
    with pytest.raises(ValueError, match='probabilities'): validate_geometry(value)


def test_native_mano_nan_sanitization_cannot_fabricate_a_valid_hand(monkeypatch):
    from mint.comparison.normalizers import mint_geometry
    from mint.visualization import mano, geometry
    monkeypatch.setattr(mano, 'decode_hand_6d', lambda a,b,c,d,e: {'trans':a,'rot':b,'hand_pose':c,'betas':d})
    monkeypatch.setattr(mano, 'run_mano', lambda *args: (np.ones((5,778,3)),np.ones((5,21,3))))
    monkeypatch.setattr(geometry,'decode_camera_pose_enc',lambda *args,**kw:(np.broadcast_to(np.eye(4),(5,4,4)).copy(),np.eye(3)))
    prediction = {'hand':np.ones((5,218),np.float32),'pose_enc':np.zeros((5,9))}
    prediction['hand'][2,0] = np.nan
    result = mint_geometry(prediction,sample())
    assert not result['geometry_valid'][2,0] and result['geometry_valid'][2,1]
    assert np.isnan(result['joints_camera_m'][2,0]).all()
    assert np.isnan(result['vertices_camera_m'][2,0]).all()
    assert validate_geometry(result) == 5


def test_durable_result_is_immutable_and_detects_replaced_array(tmp_path):
    directory = tmp_path / 'A'
    published(directory)
    metadata, value = load_result(directory)
    assert value['joints_camera_m'].shape == (5, 2, 21, 3)
    with pytest.raises(FileExistsError): published(directory)
    (directory / 'joints_camera_m.npy').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='changed'): load_result(directory)


def test_common_origin_transforms_hands_and_camera_together(tmp_path):
    directory = tmp_path / 'A'; published(directory)
    payload = frame_payload(directory, sample(), origin_source_frame=10)
    np.testing.assert_allclose(payload['camera_world_m'][0], [0, 0, 0])
    np.testing.assert_allclose(payload['joints_world_m'][0], payload['joints_camera_m'][0])
    np.testing.assert_allclose(payload['joints_world_m'][1][0][0], [1.1, 1, 1])


def test_frame_sync_never_repeats_missing_tail_or_gap(tmp_path):
    value = arrays(3)
    value['source_frame_indices'] = np.array([10, 12, 14])
    directory = tmp_path / 'A'; published(directory, value)
    payload = frame_payload(directory, sample())
    assert payload['available'] == [True, False, True, False, True]
    assert payload['joints_camera_m'][1][0][0] == [None, None, None]
    assert payload['presence'] is None
    assert json.dumps(payload, allow_nan=False)


def test_camera_combination_preserves_native_camera_geometry_and_K(tmp_path):
    hand, camera, target = [tmp_path / name for name in ('hand', 'camera', 'combined')]
    published(hand, arrays(world=False)); published(camera)
    combine_camera(hand, camera, target, metadata={'run_id': 'combined', 'label': 'hands + camera'})
    meta, value = load_result(target)
    original = load_result(hand)[1]
    np.testing.assert_array_equal(value['joints_camera_m'], original['joints_camera_m'])
    np.testing.assert_array_equal(value['K'], original['K'])
    assert meta['component_sources'] == {'hands': 'hand', 'camera': 'camera'}
    assert common_world_origin([hand, camera, target]) == 10


def test_combination_refuses_cross_sample_or_missing_world_camera(tmp_path):
    a, b = tmp_path/'a', tmp_path/'b'
    published(a); published(b, arrays(world=False))
    with pytest.raises(ValueError, match='no world'): combine_camera(a, b, tmp_path/'c', metadata={})
    other = tmp_path/'other'; published(other, sample_id='different')
    with pytest.raises(ValueError, match='exact same'): combine_camera(a, other, tmp_path/'d', metadata={})


def test_pairwise_metrics_report_coverage_and_do_not_claim_accuracy(tmp_path):
    a, b = tmp_path/'a', tmp_path/'b'
    av, bv = arrays(), arrays()
    av['joints_camera_m'] += .1
    av['geometry_valid'][0, 0] = False; av['joints_camera_m'][0, 0] = np.nan
    published(a, av); published(b, bv)
    metrics = paired_metrics(a, b)
    assert metrics['scope'] == 'algorithm_discrepancy_not_accuracy'
    assert metrics['paired_hand_slots'] == 9 and metrics['missing_first_given_second'] == 1
    assert metrics['camera_absolute_joint_mm'] == pytest.approx(np.sqrt(3) * 100, rel=1e-5)
    assert metrics['wrist_aligned_joint_mm'] == pytest.approx(0)


def test_trajectory_decimation_does_not_change_saved_data(tmp_path):
    a = tmp_path/'a'; published(a)
    data = trajectory(a, maximum=3, origin_source_frame=10)
    assert len(data['timestamps_s']) == 3 and data['display_decimation_only']
    assert load_result(a)[0]['frames'] == 5


def fake_backend(name):
    return {'id': name, 'version': name + '-v1', 'ready': True, 'camera_required': False,
            'label': name, 'identity': {}, 'root': '/tmp', 'python': '/unused', 'missing': []}


def test_parallel_duplicate_submissions_share_one_native_run(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    manager = ComparisonManager(tmp_path/'store', autostart=False)
    source = tmp_path/'v.mp4'; source.write_bytes(b'v')
    def submit(_): return manager.create(source, [{'algorithm': 'mint'}])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(submit, range(8)))
    assert len({v['id'] for v in values}) == 8
    assert len({v['run_ids'][0] for v in values}) == 1
    with manager.db() as db: assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 1
    manager.close()


def test_cancellation_and_resume_are_scoped_to_one_run(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    manager = ComparisonManager(tmp_path/'store', autostart=False)
    source = tmp_path/'v.mp4'; source.write_bytes(b'v')
    comparison = manager.create(source, [{'algorithm': 'mint'}, {'algorithm': 'ace_kfree'}])
    first, second = comparison['run_ids']
    assert manager.cancel(first)['status'] == 'cancelled'
    assert manager.run(second)['status'] == 'queued'
    assert manager.resume(first)['status'] == 'queued'
    source.write_bytes(b'replaced')
    manager.cancel(first)
    with pytest.raises(ValueError, match='Source changed'): manager.resume(first)
    manager.close()


def test_import_requires_explicit_frame_alignment(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    manager = ComparisonManager(tmp_path/'store', autostart=False)
    source = tmp_path/'v.mp4'; source.write_bytes(b'v')
    native = tmp_path/'old.npz'; native.write_bytes(b'native')
    with pytest.raises(ValueError, match='frame_map'):
        manager.create(source, [{'algorithm': 'mint'}], import_entry={'path': str(native), 'format': 'mint_npz'})
    manager.close()


def test_attach_requires_identical_input_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    manager = ComparisonManager(tmp_path/'store', autostart=False)
    source = tmp_path/'v.mp4'; source.write_bytes(b'v')
    first = manager.create(source, [{'algorithm': 'mint'}])
    second = manager.create(source, [{'algorithm': 'ace_kfree'}])
    third = manager.create(source, [{'algorithm': 'mint'}], view='left')
    merged = manager.attach(first['id'], second['run_ids'][0])
    assert merged['run_ids'] == first['run_ids'] + second['run_ids']
    assert manager.attach(first['id'], second['run_ids'][0])['run_ids'] == merged['run_ids']
    with pytest.raises(ValueError, match='exactly the same'): manager.attach(first['id'], third['run_ids'][0])
    manager.close()


def test_native_stage_markers_do_not_accept_corrupt_or_missing_stage_files(tmp_path):
    from types import SimpleNamespace
    from mint.comparison.native import stage_cached, publish_stage
    ctx = SimpleNamespace(work=tmp_path, run_id='test')
    path = tmp_path/'frame.npy'; path.write_bytes(b'whole native stage')
    publish_stage(ctx, 'motion', [path]); assert stage_cached(ctx, 'motion')
    path.write_bytes(b'corrupt'); assert not stage_cached(ctx, 'motion')
    path.unlink()
    with pytest.raises(ValueError, match='incomplete'): publish_stage(ctx, 'motion', [path])


def test_restart_preserves_unclaimed_queue_entries(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    first = ComparisonManager(tmp_path/'store', autostart=False)
    source = tmp_path/'v.mp4'; source.write_bytes(b'v')
    comparison = first.create(source, [{'algorithm': 'mint'}])
    first.close()
    second = ComparisonManager(tmp_path/'store', autostart=False)
    assert second.run(comparison['run_ids'][0])['status'] == 'queued'
    second.close()


def test_cpu_import_does_not_wait_behind_saturated_native_slots(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    entered = set(); release = threading.Event()
    def execute(self, run_id):
        entered.add(run_id); release.wait(4)
    monkeypatch.setattr(ComparisonManager, '_execute', execute)
    manager = ComparisonManager(tmp_path/'store')
    source = tmp_path/'v.mp4'; source.write_bytes(b'v')
    native = manager.create(source,[{'algorithm':'mint'},{'algorithm':'hawor_camera'},{'algorithm':'ace_kfree'}])
    file = tmp_path/'normalized.npz'; file.write_bytes(b'fixture')
    imported = manager.create(source,[{'algorithm':'mint'}],import_entry={'path':str(file),'format':'normalized_npz','frame_map':[0]})
    try:
        for _ in range(100):
            if len(entered)==4:break
            time.sleep(.02)
        assert set(native['run_ids']) | set(imported['run_ids']) == entered
    finally:
        release.set(); manager.close()


def test_durable_run_quota_never_evicts_completed_geometry(tmp_path, monkeypatch):
    from mint.comparison.storage import DurableRunBudget
    monkeypatch.setenv('MINT_COMPARISON_RUN_MAX_GIB', '.000001')
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    retained = tmp_path/'run/result/joints.npy'
    retained.parent.mkdir(parents=True); retained.write_bytes(b'x'*900)
    with pytest.raises(OSError):
        with DurableRunBudget(tmp_path).reserve(tmp_path/'new', 900):
            pass
    assert retained.read_bytes() == b'x'*900


def test_preview_quota_preserves_existing_playback_files(tmp_path, monkeypatch):
    from mint.comparison.storage import PreviewBudget
    monkeypatch.setenv('MINT_COMPARISON_PROXY_MAX_GIB', '.000001')
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    retained = tmp_path/'sample/input.mp4'
    retained.parent.mkdir(); retained.write_bytes(b'x'*900)
    with pytest.raises(OSError):
        with PreviewBudget(tmp_path).reserve(tmp_path/'new/input.mp4',900):pass
    assert retained.read_bytes()==b'x'*900


def test_proxy_quota_does_not_count_the_live_rgb_reservation(tmp_path, monkeypatch):
    from mint.comparison.storage import PreviewBudget
    from mint.inference.cache_budget import CacheBudget
    monkeypatch.setenv('MINT_COMPARISON_PROXY_MAX_GIB','.001')
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB','0')
    frames=CacheBudget(tmp_path,max_gib=1,pattern='*/frames.npy')
    with frames.reserve(tmp_path/'sample/frames.npy',10*2**20):
        with PreviewBudget(tmp_path).reserve(tmp_path/'sample/input.mp4',1024):
            assert frames.status()['reserved_bytes']==10*2**20


def test_real_decoding_publishes_full_shared_timeline_and_reuses_preview(tmp_path, monkeypatch):
    import cv2
    from mint.comparison.prepare import prepare_sample
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB','0')
    source=tmp_path/'source.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),30,(128,64))
    for i in range(7):writer.write(np.full((64,128,3),i*20,np.uint8))
    writer.release()
    request=source_request(source,view='left')
    destination=tmp_path/'inputs'/request['id']
    first=prepare_sample(request,destination)
    assert first['frames']==7 and first['source_frame_indices']==list(range(7))
    assert (first['width'],first['height'])==(64,64)
    assert np.load(first['frames_path'],mmap_mode='r').shape==(7,64,64,3)
    second=prepare_sample(request,destination)
    assert second['frames_identity']==first['frames_identity'] and second['video_identity']==first['video_identity']


def test_waiting_for_another_input_preparation_is_cancellable(tmp_path):
    from mint.comparison.prepare import preparation_lock
    from mint.inference.cache_budget import file_lock
    from mint.inference.base import InferenceCancelled
    cancel = threading.Event(); states = []; errors = []
    def wait():
        try:
            with preparation_lock(tmp_path/'lock', cancel.is_set, lambda **kw: states.append(kw)):
                errors.append('unexpected lock acquired')
        except InferenceCancelled:
            errors.append('cancelled')
    with file_lock(tmp_path/'lock'):
        thread = threading.Thread(target=wait); thread.start()
        for _ in range(50):
            if states: break
            time.sleep(.01)
        cancel.set(); thread.join(timeout=2)
        assert errors == ['cancelled'] and not thread.is_alive()


def test_camera_metrics_keep_raw_scale_error_separate_from_similarity_alignment(tmp_path):
    rng = np.random.default_rng(42)
    a, b = tmp_path/'a', tmp_path/'b'
    av, bv = arrays(), arrays()
    bv['joints_camera_m'] = rng.normal(0, .1, (5, 2, 21, 3)).astype(np.float32) + (0, 0, 2)
    av['joints_camera_m'] = bv['joints_camera_m'] * 2 + (.1, .2, .3)
    av['T_world_camera'][:, 0, 3] *= 2
    published(a, av); published(b, bv, reference_role='user_declared_ground_truth')
    metrics = paired_metrics(a, b)
    assert metrics['scope'] == 'error_against_declared_GT'
    assert metrics['camera_absolute_joint_mm'] > 1000
    assert metrics['similarity_aligned_joint_mm'] < .001
    assert metrics['camera_ate_se3_rmse_m'] > .05
    assert metrics['camera_ate_sim3_rmse_m'] < 1e-10
    assert metrics['fitted_camera_scale'] == pytest.approx(.5)
    assert metrics['camera_rpe_translation_rmse_m'] == pytest.approx(.1)


def test_geometry_export_uses_reusable_disk_archive(tmp_path, monkeypatch):
    from mint.comparison.exports import geometry_archive
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    path = tmp_path/'result'; published(path)
    archive = geometry_archive(path, tmp_path/'downloads')
    assert geometry_archive(path, tmp_path/'downloads') == archive
    with zipfile.ZipFile(archive) as z:
        assert {'result.json', 'K.npy', 'joints_camera_m.npy'} <= set(z.namelist())
        assert json.loads(z.read('result.json'))['units'] == 'm'


def test_comparison_http_frames_assets_and_exact_input_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.preflight.inspect_request', lambda *a, **kw: {'ready': True})
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    manager = ComparisonManager(tmp_path/'store', autostart=False)
    monkeypatch.setattr('mint.comparison.api.ComparisonManager', lambda: manager)
    app = Flask(__name__); app.register_blueprint(comparison_blueprint())
    client = app.test_client()
    assert client.get('/compare').status_code == 200
    assert client.get('/compare/?id=kept').location == '/compare?id=kept'
    assert client.get('/comparison-assets/app.js').status_code == 200
    source = tmp_path/'v.mp4'; source.write_bytes(b'v')
    response = client.post('/api/comparisons', json={'source': str(source), 'selections': [{'algorithm': 'mint'}]})
    assert response.status_code == 201
    comp = response.json; run_id = comp['run_ids'][0]
    directory = manager.root/'runs'/run_id/'result'
    published(directory, sample_id=comp['sample_id'], run_id=run_id)
    manager._set(run_id, 'completed')
    sample_path = manager.root/'inputs'/comp['sample_id']/'sample.json'
    sample_path.parent.mkdir(parents=True); sample_path.write_text(json.dumps(sample()))
    frames = client.get('/api/comparison/runs/'+run_id+'/frames?count=3')
    assert frames.status_code == 200 and frames.json['source_frame_indices'] == [10, 11, 12]
    assert client.get('/api/comparison/runs/'+run_id+'/frames?count=257').status_code == 400
    assert client.post('/api/comparisons/'+comp['id']+'/attach', json={'run_id': run_id}).json['run_ids'] == [run_id]
    assert client.post('/api/comparisons', json={'source': str(source), 'selections': [], 'unknown': 3}).status_code == 400
    manager.close()
