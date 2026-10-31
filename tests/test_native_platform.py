"""Scientific provenance, correction concurrency and reproducible study boundaries."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from mint.runtime import atomic_json, digest, file_identity
from mint.comparison.calibration import pinhole_matrix, rectification_maps, resize_intrinsics, validate_pose_matrices
from mint.comparison.diagnostics import error_breakdown
from mint.comparison.native_sources import inside, source_plan, prepare_native
from mint.comparison.annotations import AnnotationService
from mint.comparison.research import ResearchRegistry
from mint.comparison.studies import StudyManager
from mint.comparison.training_curves import append_metrics, read_curves
from mint.comparison.distributed import load_profile, node_command
from tests.test_platform_lifecycle import manager, comparison
from tests.test_comparison import arrays, sample, fake_backend


def test_rectification_rays_match_scaled_camera_and_keep_camera_frame():
    import cv2
    K = np.array([[520., 0, 311], [0, 540, 233], [0, 0, 1]])
    points = np.array([[.12, .06, .8], [-.08, .15, .6], [0., 0., .5]])
    distortion = np.array([.08, -.03, .001, -.002, .01, .002, 0, 0])
    target, maps = rectification_maps(K, distortion, (480, 640), (240, 320))
    original, _ = cv2.projectPoints(points, np.zeros(3), np.zeros(3), K, distortion)
    rectified = cv2.undistortPoints(original, K, distortion, P=target)[:, 0]
    expected = (target @ points.T).T; expected = expected[:, :2] / expected[:, 2:]
    np.testing.assert_allclose(rectified, expected, atol=1e-6)
    assert target[0, 2] == 160 and target[1, 2] == 120 and maps[0].shape == (240, 320)
    np.testing.assert_allclose(resize_intrinsics(K, (480, 640), (240, 320))[:2], K[:2] / 2)
    with pytest.raises(ValueError):
        validate_pose_matrices(np.zeros((2, 4, 4)))
    with pytest.raises(ValueError):
        pinhole_matrix([[1, .2, 0], [0, 1, 0], [0, 0, 1]])


def test_native_partition_and_path_boundaries_are_not_guessed(tmp_path):
    with pytest.raises(ValueError):
        inside(tmp_path, '../another')
    (tmp_path / 'link').symlink_to(tmp_path.parent)
    with pytest.raises(ValueError):
        inside(tmp_path, 'link/outside')
    with pytest.raises(ValueError, match='official train'):
        source_plan('egodex', tmp_path, 'test/action/0', split='test', training=True)
    with pytest.raises(ValueError, match='partition'):
        source_plan('egodex', tmp_path, 'test/action/0', split='train')
    with pytest.raises(ValueError, match='integers'):
        source_plan('egodex', tmp_path, 'test/action/0', max_frames=True)
    with pytest.raises(ValueError, match='official native partition'):
        source_plan('egodex', tmp_path, 'test/action/0', split='val')
    with pytest.raises(ValueError, match='joint GT'):
        source_plan('dexycb', tmp_path, 'subject/sequence/camera', split='train', training=True)


def test_native_egodex_preserves_full_pixels_confidence_and_source_changes(tmp_path):
    h5py = pytest.importorskip('h5py')
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    seq = tmp_path / 'test/action/0'; seq.parent.mkdir(parents=True)
    rgb = np.random.default_rng(42).integers(0, 256, (4, 32, 48, 3), dtype=np.uint8)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '48x32',
        '-r', '30', '-i', 'pipe:0', '-c:v', 'libx264rgb', '-crf', '0', str(seq.with_suffix('.mp4'))], input=rgb.tobytes(), check=True)
    from mint.comparison.native_sources import EGO_JOINTS
    with h5py.File(seq.with_suffix('.hdf5'), 'w') as data:
        data['camera/intrinsic'] = [[45, 0, 24], [0, 45, 16], [0, 0, 1]]
        poses = np.broadcast_to(np.eye(4), (4, 4, 4)).copy(); poses[:, 0, 3] = np.arange(4) * .01
        data['transforms/camera'] = poses
        for side in ('left', 'right'):
            for key in EGO_JOINTS:
                hand = poses.copy(); hand[:, 2, 3] = .5
                data['transforms/' + side + key] = hand
                data['confidences/' + side + key] = [1., 0., 1., 1.] if side == 'left' else np.ones(4)
    plan = source_plan('egodex', tmp_path, 'test/action/0')
    folder = tmp_path / 'owned' / digest(plan)
    result = prepare_native(plan, folder)
    assert result['frames'] == 4 and result['valid_gt_hand_frames'] == [3, 4]
    assert (folder / 'video.mp4').read_bytes() == seq.with_suffix('.mp4').read_bytes()
    with np.load(result['reference']) as data:
        assert np.isnan(data['joints_camera_m'][1, 0]).all()
        np.testing.assert_allclose(data['joints_camera_m'][0, 1, :, 2], .5)
    assert prepare_native(plan, folder)['id'] == result['id']
    seq.with_suffix('.mp4').touch()
    with pytest.raises(ValueError, match='changed'):
        prepare_native(plan, folder)


def test_breakdown_does_not_equate_finite_geometry_with_presence():
    prediction, reference = arrays(3, world=False), arrays(3, world=False)
    prediction['presence'] = np.zeros((3, 2), bool)
    prediction['joints_camera_m'][:, 0, :, 0] += .01
    result = error_breakdown(prediction, reference, image_hw=(480, 640))
    assert result['groups']['all']['geometry_coverage'] == 1
    assert result['groups']['all']['detection_recall_on_labelled_positive_hands'] == 0
    assert result['groups']['all']['detection_precision'] is None
    assert result['groups']['left']['absolute_joint_mm']['mean'] == pytest.approx(10., abs=.001)
    assert result['groups']['left']['wrist_aligned_joint_mm']['mean'] == pytest.approx(0)
    assert result['groups']['depth_ge_100cm']['gt_hand_frames'] == 6
    assert result['joints'][0]['paired_hand_frames'] == 6


def test_2d_correction_is_atomic_versioned_and_cannot_rewrite_geometry(manager, tmp_path):
    comp = comparison(manager, tmp_path)
    run = comp['run_ids'][0]
    source = comp['sample_request']['source']
    atomic_json(manager.root / 'inputs' / comp['sample_id'] / 'sample.json',
                {**sample(), 'id': comp['sample_id'], 'request': {'source': source}})
    registry = ResearchRegistry(manager); service = AnnotationService(manager, registry)
    original = (manager.root / 'runs' / run / 'result/result.json').read_bytes()
    points = np.tile([100., 200.], (2, 21, 1)); visible = np.ones((2, 21), bool)
    a = service.save(run_id=run, frame=0, points=points, visible=visible, reviewer='QA')
    points[0, 4] = [105, 210]
    with pytest.raises(ValueError, match='changed'):
        service.save(run_id=run, frame=0, points=points, visible=visible, reviewer='QA', parent=None)
    b = service.save(run_id=run, frame=0, points=points, visible=visible, reviewer='QA', parent=a['id'], hand_assignment=['right', 'left'])
    assert b['parent'] == a['id'] and registry.get(a['id'])['points'][0][4] == [100., 200.]
    assert service.context(run, 0)['head'] == b['id']
    assert [v['id'] for v in service.history(run, 0)['revisions']] == [b['id'], a['id']]
    assert (manager.root / 'runs' / run / 'result/result.json').read_bytes() == original
    points[0, 4] = [-5, 10]
    with pytest.raises(ValueError, match='inside'):
        service.save(run_id=run, frame=0, points=points, visible=visible, reviewer='QA', parent=b['id'])
    visible[0, 4] = False
    assert service.save(run_id=run, frame=0, points=points, visible=visible, reviewer='QA', parent=b['id'])['label_role'] == 'human_corrected_2d_only'


def test_curves_handle_restart_duplicate_steps_and_truncated_last_line(tmp_path):
    from types import SimpleNamespace
    root = tmp_path / 'workflows'; key = 'training'; folder = root / key; folder.mkdir(parents=True)
    path = folder / 'metrics.jsonl'
    append_metrics(path, 1, {'loss': 4, 'invalid': float('nan')}, attempt='1')
    append_metrics(path, 2, {'loss': 3}, attempt='1')
    append_metrics(path, 2, {'loss': 2.5}, attempt='2')
    with path.open('a') as stream:
        stream.write('{"step":3')
    result = read_curves(SimpleNamespace(root=root, get=lambda _: {'status': 'interrupted'}), key)
    assert result['available_points'] == 2 and result['points'][1]['metrics']['loss'] == 2.5
    assert result['attempts'] == ['1', '2'] and 'invalid' not in result['points'][0]['metrics']
    append_metrics(path, 3, {'loss': 2}, attempt='2')
    assert read_curves(SimpleNamespace(root=root, get=lambda _: {'status': 'running'}), key)['available_points'] == 3


def test_fresh_study_children_are_idempotent_across_coordinator_restarts(manager, tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'; source.write_bytes(b'fixture')
    a = manager.create(source, [{'algorithm': 'mint'}], comparison_key='fixed-fresh', execution={'cache': 'fresh'})
    b = manager.create(source, [{'algorithm': 'mint'}], comparison_key='fixed-fresh', execution={'cache': 'fresh'})
    assert a['run_ids'] == b['run_ids']
    c = manager.create(source, [{'algorithm': 'mint'}], execution={'cache': 'fresh'})
    assert c['run_ids'] != a['run_ids']
    monkeypatch.setattr('mint.comparison.studies.backend_spec', fake_backend)
    studies = StudyManager(manager, ResearchRegistry(manager), None, autostart=False)
    with pytest.raises(ValueError, match='integer GPU'):
        studies.create(kind='performance', selections=[{'algorithm': 'mint'}], comparison_id=a['id'], gpu_counts=[True])
    job = studies.create(kind='performance', selections=[{'algorithm': 'mint'}], comparison_id=a['id'], gpu_counts=[1, 2])
    studies._set(job['id'], status='running', owner_pid=999999999, owner_token='dead')
    restored = StudyManager(manager, ResearchRegistry(manager), None, autostart=False)
    assert restored.get(job['id'])['status'] == 'interrupted'
    assert restored.resume(job['id'])['status'] == 'queued'
    studies.close(); restored.close()


def test_distributed_profile_has_explicit_execution_and_safe_shell_quoting(tmp_path):
    root = tmp_path / 'mint space'; root.mkdir()
    profile = {'shared_root': str(root), 'master_address': 'node1', 'master_port': 29500,
               'nodes': [{'host': 'node1', 'rank': 0, 'gpus': 2}, {'host': 'node2', 'rank': 1, 'gpus': 2}]}
    path = tmp_path / 'profile.json'; path.write_text(json.dumps(profile))
    loaded = load_profile(path)
    command = node_command(loaded, loaded['nodes'][1], ['--probe', str(root / 'config.yaml'), '--root', str(root)])
    assert command[0] == 'ssh' and "'" in command[-1] and '--probe' in command[-1]
    profile['nodes'][1]['host'] = '-oProxyCommand=oops'; path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match='hostname'):
        load_profile(path)


def test_cancelled_study_cannot_be_relaunched_by_a_stale_claim(manager, tmp_path, monkeypatch):
    comp = comparison(manager, tmp_path)
    monkeypatch.setattr('mint.comparison.studies.backend_spec', fake_backend)
    studies = StudyManager(manager, ResearchRegistry(manager), None, autostart=False)
    job = studies.create(kind='performance', selections=[{'algorithm': 'mint'}], comparison_id=comp['id'], gpu_counts=[1])
    assert studies.cancel(job['id'])['status'] == 'cancelled'
    studies._execute(job['id'])
    assert studies.get(job['id'])['status'] == 'cancelled'
    studies._set(job['id'], status='completed')
    assert studies.cancel(job['id'])['status'] == 'completed'


def test_model_generated_training_labels_require_explicit_opt_in(manager, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from mint.comparison.workflows import WorkflowManager
    root = tmp_path / 'pseudo'; (root / 'meta').mkdir(parents=True)
    atomic_json(root / 'meta/info.json', {'hand_supervision': 'pseudo_mano'})
    config = tmp_path / 'config.yaml'
    config.write_text('data:\n  root: ' + str(root) + '\n  require_mano_gt: false\nmodel: {}\ntrain: {}\n')
    monkeypatch.setattr('mint.comparison.workflows.owned_environment', lambda _: Path(sys.executable))
    monkeypatch.setattr('mint.comparison.workflows.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"missing":[],"versions":{}}'))
    monkeypatch.setattr('mint.readiness.training_inputs', lambda *a, **k: [])
    workflows = WorkflowManager(manager, ResearchRegistry(manager), autostart=False)
    denied = workflows.preflight('train', {'config': str(config), 'allow_pseudo_labels': False})
    assert not denied['ready'] and any('allow_pseudo_labels' in error for error in denied['errors'])
    allowed = workflows.preflight('train', {'config': str(config), 'allow_pseudo_labels': True})
    assert allowed['ready'], allowed['errors']
    workflows.close()


def test_native_origin_survives_legacy_converted_and_pseudo_versions(manager, tmp_path):
    from mint.comparison.native_sources import native_source_key, source_provenance
    from mint.comparison.research import training_source_keys
    comp = comparison(manager, tmp_path)
    source = comp['sample_request']['source']
    native = {'plan': {'dataset': 'egodex', 'root': '/read-only/egodex', 'sequence': 'test/action/0', 'split': 'test'},
              'source': source['path']}
    registry = ResearchRegistry(manager)
    registry.put('dataset', {'name': 'native origin fixture', 'native': native})
    expected = native_source_key(native['plan'])
    assert source_provenance(manager, source)['source_key'] == expected
    assert expected in training_source_keys(manager, {'source_keys': ['legacy-key'], 'native': native})
    assert expected in training_source_keys(manager, {'provenance': {'label_origin': {'plan': [{'sample': {'source': source}}]}}})
    assert expected in training_source_keys(manager, {'entries': [{'run_id': comp['run_ids'][0]}]})


def test_durable_quota_counts_owned_store_links_without_external_scans(tmp_path, monkeypatch):
    from mint.comparison.storage import DurableRunBudget
    owned = tmp_path / 'mint'; old = owned / 'archive' / 'run'; old.mkdir(parents=True)
    data = old / 'geometry.npy'; data.write_bytes(b'immutable geometry')
    before = file_identity(data)
    target = owned / 'runs'; target.mkdir()
    (target / 'first').symlink_to(old, target_is_directory=True)
    (target / 'same-store').symlink_to(old, target_is_directory=True)
    external = tmp_path / 'external'; external.mkdir(); (external / 'large').write_bytes(b'x' * 1000)
    (target / 'external').symlink_to(external, target_is_directory=True)
    (old / 'cycle').symlink_to(target, target_is_directory=True)
    monkeypatch.setattr('mint.comparison.storage.OWNED_ROOT', owned)
    total, removed = DurableRunBudget(target)._evict(0, 0)
    assert total == len(b'immutable geometry') and not removed
    assert file_identity(data) == before
