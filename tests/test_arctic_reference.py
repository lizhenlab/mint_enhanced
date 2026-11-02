"""ARCTIC mean-relative poses and original left MANO basis are not MINT poses.

The numerical oracle uses a generated toy MANO model, so no licensed MANO
weights or dataset download is needed by the regression suite.
"""
import json
import pickle

import numpy as np
import pytest

from mint.comparison import native_sources as native
from tests.test_platform_lifecycle import manager


@pytest.fixture
def mano_models(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    pytest.importorskip('smplx')
    from mint.visualization import mano
    rng = np.random.default_rng(46)
    parents = np.array([-1, 0, 1, 2, 0, 4, 5, 0, 7, 8, 0, 10, 11, 0, 13, 14])
    regressor = np.zeros((16, 778), np.float32)
    regressor[np.arange(16), np.arange(16)] = 1
    weights = rng.uniform(.1, 1., (778, 16)).astype(np.float32)
    weights /= weights.sum(axis=1, keepdims=True)
    values = dict(v_template=rng.normal(0, .03, (778, 3)).astype(np.float32),
                  shapedirs=rng.normal(0, .002, (778, 3, 10)).astype(np.float32),
                  posedirs=rng.normal(0, .001, (778, 3, 135)).astype(np.float32),
                  J_regressor=regressor, weights=weights,
                  kintree_table=np.stack([parents, np.arange(16)]),
                  f=np.array([[0, 1, 2], [2, 3, 4]], np.int64),
                  hands_components=np.eye(45, dtype=np.float32),
                  hands_mean=np.linspace(-.35, .45, 45, dtype=np.float32))
    for side in ('left', 'right'):
        folder = tmp_path / side; folder.mkdir()
        with (folder / ('MANO_' + side.upper() + '.pkl')).open('wb') as stream:
            pickle.dump(values, stream)
        monkeypatch.setattr(mano, '_MANO_' + side.upper() + '_DIR', folder)
    monkeypatch.setenv('MINT_MANO_BATCH_SIZE', '2')
    return mano, torch


@pytest.mark.parametrize('is_right', [False, True])
def test_official_mean_pose_and_unflipped_shape_match_axis_angle_oracle(mano_models, is_right):
    import smplx
    mano, torch = mano_models
    rng = np.random.default_rng(48)
    trans = rng.normal(0, .2, (5, 3)).astype(np.float32)
    rot = rng.normal(0, .1, (5, 3)).astype(np.float32)
    pose = rng.normal(0, .15, (5, 45)).astype(np.float32)
    original_pose = pose.copy()
    beta = rng.normal(0, .5, (5, 10)).astype(np.float32)
    folder = mano._MANO_RIGHT_DIR if is_right else mano._MANO_LEFT_DIR
    oracle = smplx.MANO(str(folder), is_rhand=is_right, use_pca=False, flat_hand_mean=False)
    with torch.no_grad():
        expected = oracle(transl=torch.from_numpy(trans), global_orient=torch.from_numpy(rot),
                          hand_pose=torch.from_numpy(pose), betas=torch.from_numpy(beta))
    cached_before = dict(mano._MANO_MODEL_CACHE)
    device_before = mano._DEVICE
    vertices, joints, absolute = native._arctic_mano(trans, rot, pose, beta, is_right)
    np.testing.assert_allclose(vertices, expected.vertices.numpy(), atol=2e-7, rtol=2e-5)
    tips = expected.vertices[:, oracle.vertex_joint_selector.extra_joints_idxs].numpy()
    raw_joints = np.concatenate([expected.joints.numpy(), tips], axis=1)
    expected_joints = raw_joints[:, [0, 13, 14, 15, 16, 1, 2, 3, 17, 4, 5, 6, 18, 10, 11, 12, 19, 7, 8, 9, 20]]
    np.testing.assert_allclose(joints, expected_joints, atol=2e-7, rtol=2e-5)
    np.testing.assert_allclose(absolute, pose + oracle.hand_mean.numpy())
    # Neither the renderer's global models nor its CPU/GPU device are touched.
    assert mano._MANO_MODEL_CACHE == cached_before and mano._DEVICE is device_before
    np.testing.assert_array_equal(pose, original_pose)


def test_official_decode_checks_cancellation_before_each_cpu_batch(mano_models):
    calls = []
    def cancelled():
        calls.append(True)
        return len(calls) > 1
    with pytest.raises(InterruptedError, match='cancelled'):
        native._arctic_mano(np.zeros((5, 3)), np.zeros((5, 3)), np.zeros((5, 45)),
                            np.zeros((5, 10)), True, cancelled=cancelled)
    assert len(calls) == 2


@pytest.mark.parametrize('training', [False, True])
def test_arctic_training_plan_cannot_claim_mint_mano_compatibility(tmp_path, training):
    if training:
        with pytest.raises(ValueError, match='shape 基'):
            native.source_plan('arctic', tmp_path, 's05/box', split='train', training=True)
        with pytest.raises(ValueError, match='shape 基'):
            native._prepare_native({'dataset': 'arctic', 'training': True}, tmp_path / 'owned')
        assert not (tmp_path / 'owned').exists()
    else:
        (tmp_path / 'DATASET_CATALOG_20261016.json').write_text(json.dumps({'entries': [
            {'dataset': 'ARCTIC', 'path': str(tmp_path)}]}))
        row = native.dataset_catalog(tmp_path)['datasets'][0]
        assert row['training_export'] is False and 'shape 基' in row['training_export_issue']


def test_old_arctic_reference_cannot_be_reused_as_a_verified_data_version(tmp_path):
    folder = tmp_path / 'owned'; folder.mkdir()
    plan = {'dataset': 'arctic', 'training': False, 'files': []}
    (folder / 'native.json').write_text(json.dumps({'plan': plan, 'details': {'label_role': 'official_MANO_annotation'}}))
    with pytest.raises(ValueError, match='重新准备数据版本'):
        native.prepare_native(plan, folder)


def test_arctic_camera_geometry_and_export_pose_share_official_absolute_rotations(tmp_path, monkeypatch):
    cv2 = pytest.importorskip('cv2')
    Rotation = pytest.importorskip('scipy.spatial.transform').Rotation
    from mint.visualization import geometry
    root = tmp_path / 'dataset'; sequence = 's05/box'
    raw = root / 'data/raw_seqs/s05'; raw.mkdir(parents=True)
    image_dir = root / 'data/cropped_images' / sequence / '0'; image_dir.mkdir(parents=True)
    meta = root / 'data/meta'; meta.mkdir(parents=True)
    (meta / 'misc.json').write_text(json.dumps({'s05': {'ioi_offset': 1, 'image_size': [[40, 30]]}}))
    hand = {'shape': np.arange(10, dtype=np.float32), 'rot': np.tile([.03, -.07, .11], (2, 1)),
            'pose': np.zeros((2, 45)), 'trans': np.tile([.2, .3, .4], (2, 1))}
    np.save(raw / 'box.mano.npy', {'left': hand, 'right': hand})
    R = Rotation.from_rotvec([.1, -.2, .3]).as_matrix()
    t = np.array([.03, -.02, .7])
    np.save(raw / 'box.egocam.dist.npy', {'R_k_cam_np': np.tile(R, (2, 1, 1)),
        'T_k_cam_np': np.tile(t[:, None], (2, 1, 1)), 'intrinsics': [[35, 0, 20], [0, 35, 15], [0, 0, 1]], 'dist8': np.zeros(8)})
    for frame in (1, 2):
        assert cv2.imwrite(str(image_dir / f'{frame:05}.jpg'), np.zeros((30, 40, 3), np.uint8))
    absolute = np.tile(np.linspace(-.2, .4, 45), (2, 1)).astype(np.float32)
    j = np.tile([.1, .05, .3], (2, 21, 1)).astype(np.float32)
    v = np.tile([.08, .03, .32], (2, 778, 1)).astype(np.float32)
    monkeypatch.setattr(native, '_arctic_mano', lambda *args, **kwargs: (v.copy(), j.copy(), absolute.copy()))
    pixels = []
    monkeypatch.setattr(native, '_encode', lambda images, *_: pixels.extend(images))
    arrays, K, ids, fps, shape, details, hands = native._arctic(
        {'root': str(root), 'sequence': sequence, 'start_frame': 0, 'max_frames': None}, tmp_path, lambda: False, lambda **_: None)
    expected_j = np.einsum('ij,tkj->tki', R, j) + t
    np.testing.assert_allclose(arrays['joints_camera_m'][:, 0], expected_j, atol=1e-7)
    np.testing.assert_allclose(hands[:, 0, :3], expected_j[:, 0], atol=1e-7)
    for slot in (0, 1):
        pose = absolute.copy()
        if slot == 0:
            pose[:, 1::3] *= -1; pose[:, 2::3] *= -1
        expected_pose = Rotation.from_rotvec(pose.reshape(-1, 3)).as_matrix().reshape(2, 15, 3, 3)
        np.testing.assert_allclose(hands[:, slot, 9:99], geometry.mat_to_6d(expected_pose).reshape(2, 90), atol=1e-7)
    assert details['mano_convention'] == native.ARCTIC_MANO_CONVENTION
    assert details['mint_mano_training_compatible'] is False
    assert ids.tolist() == [1, 2] and fps == 30 and shape == (480, 672) and len(pixels) == 2


def arctic_training_source(tmp_path, *, hand_supervision='mano', conversions=0):
    from mint.runtime import atomic_json
    root = tmp_path / 'arctic-original'; (root / 'meta').mkdir(parents=True)
    atomic_json(root / 'meta/info.json', {'hand_supervision': hand_supervision})
    atomic_json(root / 'source_manifest.json', {'label_origin': {
        'type': 'official_MANO_annotation', 'native_dataset': 'arctic'}, 'cam_fov_order': 'height_width'})
    for index in range(conversions):
        converted = tmp_path / f'converted-{index}'; (converted / 'meta').mkdir(parents=True)
        atomic_json(converted / 'meta/info.json', {'hand_supervision': 'none'})
        atomic_json(converted / 'source_manifest.json', {'label_origin': {
            'type': 'existing_declared_camera_annotations', 'source': str(root)}, 'cam_fov_order': 'height_width'})
        root = converted
    return root


@pytest.mark.parametrize('conversions', [0, 2])
def test_incompatible_arctic_mano_cannot_be_trained_or_laundered_by_conversion(manager, tmp_path, monkeypatch, conversions):
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from mint.comparison import datasets
    from mint.comparison.research import ResearchRegistry
    from mint.comparison.workflows import WorkflowManager
    from mint.comparison.training_data import mano_training_schema_errors, prepare_existing_camera_dataset
    root = arctic_training_source(tmp_path, conversions=conversions)
    errors = mano_training_schema_errors(root)
    assert errors and 'shape 基' in errors[0]
    destination = tmp_path / 'another-conversion'
    with pytest.raises(ValueError, match='shape 基'):
        prepare_existing_camera_dataset(root, destination)
    assert not destination.exists()
    monkeypatch.setattr(datasets, 'dataset_info', lambda _: (root, {'fps': 30}, ['ego']))
    monkeypatch.setattr(datasets, 'select_episode', lambda _: (root, {}, 'ego', {}, {'camera': True}))
    monkeypatch.setattr(datasets, 'annotation_identity', lambda *args: {})
    monkeypatch.setattr('mint.comparison.workflows.owned_environment', lambda _: Path(sys.executable))
    monkeypatch.setattr('mint.comparison.workflows.subprocess.run', lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout='{"missing":[],"versions":{}}'))
    config = tmp_path / 'train.yaml'
    config.write_text('data:\n  root: ' + str(root) + '\n  require_mano_gt: false\nmodel: {}\ntrain: {}\n')
    workflows = WorkflowManager(manager, ResearchRegistry(manager), autostart=False)
    try:
        for kind, parameters in [('train', {'config': str(config)}), ('prepare_training', {'source': str(root)})]:
            denied = workflows.preflight(kind, parameters)
            assert not denied['ready'] and any('shape 基' in error for error in denied['errors'])
    finally:
        workflows.close()


@pytest.mark.parametrize('conversions', [0, 2])
def test_genuine_camera_only_arctic_has_no_incompatible_mano_labels(tmp_path, conversions):
    from mint.comparison.training_data import mano_training_schema_errors
    root = arctic_training_source(tmp_path, hand_supervision='none', conversions=conversions)
    assert mano_training_schema_errors(root) == []
