"""CPU-only inspection: exact frames, native provenance and bounded safe reads."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mint.comparison.contracts import publish_result
from mint.comparison.parameters import frame_parameters
from mint.comparison.results import frame_payload
from mint.runtime import atomic_json, atomic_npz, file_identity


@pytest.fixture
def fixture(tmp_path):
    def create(algorithm='mint', n=5, *, imported=False, world=True, positions=None, detected=None):
        run_id, sample_id = 'a' * 64, 'b' * 64
        folder = tmp_path / 'runs' / run_id
        native = folder / 'native'; native.mkdir(parents=True)
        sample = dict(id=sample_id, frames=n, width=640, height=480, fps=30,
                      source_frame_indices=(np.arange(n) * 3 + 10).tolist(),
                      timestamps_s=(np.arange(n) / 10 + 2).tolist())
        atomic_json(tmp_path / 'inputs' / sample_id / 'sample.json', sample)
        atomic_json(folder / 'request.json', dict(id=run_id, sample_id=sample_id, algorithm=algorithm))
        positions = np.arange(n) if positions is None else np.asarray(positions)
        count = len(positions)
        arrays = dict(joints_camera_m=np.ones((count, 2, 21, 3), np.float32),
            geometry_valid=np.ones((count, 2), bool),
            source_frame_indices=np.asarray(sample['source_frame_indices'])[positions],
            timestamps_s=np.asarray(sample['timestamps_s'])[positions],
            K=np.tile(np.array([[500., 0, 320], [0, 500, 240], [0, 0, 1]]), (count, 1, 1)))
        if detected is not None:
            arrays['detected'] = np.asarray(detected, bool)
        if world:
            T = np.tile(np.eye(4), (count, 1, 1)); T[:, 0, 3] = positions + 5
            arrays.update(T_world_camera=T, pose_valid=np.ones(count, bool))
        metadata = publish_result(folder / 'result', arrays, dict(algorithm=algorithm, run_id=run_id,
            sample_id=sample_id, label=algorithm))
        row = dict(id=run_id, sample_id=sample_id, algorithm=algorithm, imported=imported, result=metadata)
        manager = SimpleNamespace(root=tmp_path, run=lambda key: row)
        return SimpleNamespace(manager=manager, row=row, run_id=run_id, sample=sample,
                               folder=folder, native=native, arrays=arrays)
    return create


def mint(value, *, optional=True):
    n = value.sample['frames']
    hand = np.arange(n * 218, dtype=np.float32).reshape(n, 218)
    fields = dict(hand=hand, pose_enc=np.arange(n * 9, dtype=np.float32).reshape(n, 9))
    if optional:
        fields['hand_presence_logits'] = np.tile([[-2., 3.]], (n, 1))
    atomic_npz(value.native / 'mint_native.npz', **fields)
    return hand


def inspect(value, frame=0, **kwargs):
    return frame_parameters(value.manager, value.run_id, frame, **kwargs)


def by_name(payload):
    return {field['name']: field for field in payload['native']['fields']}


def test_mint_exact_sample_frame_left_right_and_optional_logits(fixture):
    value = fixture(); hand = mint(value)
    payload = inspect(value, 2)
    assert payload['sample_position'] == 2 and payload['source_frame_index'] == 16
    assert payload['timestamp_s'] == 2.2 and payload['native']['available']
    fields = by_name(payload)
    np.testing.assert_array_equal(fields['hand109']['value'], hand[2].reshape(2, 109))
    np.testing.assert_array_equal(fields['transl_cam']['value'][1], hand[2, 109:112])
    assert fields['pose6d']['shape'] == [2, 15, 6]
    assert 'not the wrist' in fields['transl_cam']['note']
    assert fields['hand_presence_logits']['value'] == [-2., 3.]
    assert payload['standard']['presence'] is None  # No invented presence.


def test_optional_native_field_missing_is_not_synthesized(fixture):
    value = fixture(world=False); mint(value, optional=False)
    payload = inspect(value)
    assert payload['native']['available'] and 'hand_presence_logits' not in by_name(payload)
    assert payload['standard']['pose_valid'] is None and payload['standard']['T_world_camera'] is None


def test_detected_preserves_hand_slots_and_missing_frame_availability(fixture):
    value = fixture(positions=[0, 2, 3, 4], detected=[[True, False], [False, True], [True, True], [False, False]])
    payload = frame_payload(value.folder / 'result', value.sample, 0, 3)
    assert payload['available'] == [True, False, True]
    assert payload['detected'] == [[True, False], [False, False], [False, True]]
    assert inspect(value, 0)['standard']['detected'] == [True, False]
    missing = inspect(value, 1)['standard']
    assert missing['available'] is False and missing['detected'] == [False, False]
    absent = dict(value.arrays); absent.pop('detected')
    # A separate immutable result without this optional status keeps null.
    folder = value.folder / 'without-detected'
    publish_result(folder, absent, value.row['result'])
    assert frame_payload(folder, value.sample, 0, 1)['detected'] is None


@pytest.mark.parametrize('frame', [-1, 5, 1.5, True, 'NaN'])
def test_frame_bounds_and_integer_validation(fixture, frame):
    value = fixture(); mint(value)
    with pytest.raises(ValueError):
        inspect(value, frame)


def test_common_origin_complete_pose_and_raw_native_are_distinct(fixture):
    value = fixture(); mint(value)
    native = inspect(value, 3)
    rebased = inspect(value, 3, origin_source_frame=13)
    assert rebased['standard']['T_world_camera'][0][3] == 2
    assert rebased['standard']['pose_valid'] is True
    assert native['native'] == rebased['native']
    frames = frame_payload(value.folder / 'result', value.sample, 3, 1, origin_source_frame=13)
    assert frames['timestamps_s'] == [2.3]
    assert frames['T_world_camera'][0] == rebased['standard']['T_world_camera']
    with pytest.raises(ValueError, match='origin'):
        inspect(value, 3, origin_source_frame=999)


@pytest.mark.parametrize('kind', ['missing', 'imported', 'derived', 'wrong_request', 'missing_frame'])
def test_unavailable_sources_never_substitute_native(fixture, kind):
    value = fixture(imported=kind == 'imported', algorithm='camera_combination' if kind == 'derived' else 'mint',
                    positions=[0, 2, 3, 4] if kind == 'missing_frame' else None)
    if kind != 'missing':
        mint(value)
    if kind == 'wrong_request':
        atomic_json(value.folder / 'request.json', dict(id='c' * 64, sample_id=value.row['sample_id'], algorithm='mint'))
    payload = inspect(value, 1)
    assert payload['native']['available'] is False and payload['native']['fields'] == []
    assert payload['native']['reason']
    if kind == 'missing_frame':
        assert payload['standard']['available'] is False


def ace_segment(value, index, ids, *, slot_order=(0., 1.), optional=True):
    count = len(ids)
    fields = dict(frame_indices=np.asarray(ids), run_id=np.asarray(value.run_id),
        go=np.tile(np.eye(3), (count, 2, 1, 1, 1)),
        hp=np.tile(np.eye(3), (count, 2, 15, 1, 1)),
        betas=np.full((count, 2, 10), index + 1.), cam_trans=np.full((count, 2, 3), index + 2.),
        is_right=np.tile(slot_order, (count, 1)), K_matrix=np.eye(3))
    if optional:
        fields.update(exists_3d=np.tile([[.2, .8]], (count, 1)))
    path = value.native / f'ace_segment_{index:06d}.npz'
    atomic_npz(path, **fields)
    return path


def test_ace_cross_segment_mapping_and_overlap_are_explicit(fixture):
    value = fixture('ace_kfree', n=7, world=False)
    ace_segment(value, 0, [0, 1, 2, 3]); ace_segment(value, 1, [3, 4, 5, 6], optional=False)
    payload = inspect(value, 3); fields = by_name(payload)
    assert payload['native']['available']
    assert fields['ace_segment_000000.betas']['value'][0][0] == 1.
    assert fields['ace_segment_000001.betas']['value'][0][0] == 2.
    assert 'before native segment merge' in fields['ace_segment_000001.go']['note']
    later = by_name(inspect(value, 5))
    assert all(name.startswith('ace_segment_000001.') for name in later)
    assert 'ace_segment_000001.exists_3d' not in later


def test_ace_slot_order_and_run_identity_must_be_proven(fixture):
    value = fixture('ace_kgiven', world=False)
    path = ace_segment(value, 0, [0, 1, 2, 3, 4], slot_order=(1., 0.))
    assert 'slot order' in inspect(value)['native']['reason']
    with np.load(path, allow_pickle=False) as source:
        fields = {key: source[key] for key in source.files}
    fields['is_right'] = np.tile([0., 1.], (5, 1)); fields['run_id'] = np.asarray('c' * 64)
    atomic_npz(path, **fields)
    assert 'another run' in inspect(value)['native']['reason']


def test_hawor_world_axes_preserve_slots_and_origin(fixture):
    value = fixture('hawor_full'); n = value.sample['frames']
    trans = np.arange(2 * n * 3).reshape(2, n, 3)
    atomic_npz(value.native / 'hawor_world_native.npz', translation=trans,
        root_rotation=np.zeros((2, n, 3)), hand_pose=np.zeros((2, n, 45)), shape=np.zeros((2, n, 10)),
        valid=np.ones((2, n), bool), camera_rotation_c2w=np.tile(np.eye(3), (n, 1, 1)),
        camera_translation_c2w=np.zeros((n, 3)), hand_order=np.asarray(['left', 'right']))
    payload = inspect(value, 2, origin_source_frame=10)
    assert payload['native']['available']
    np.testing.assert_array_equal(by_name(payload)['translation']['value'], trans[:, 2])
    assert 'safe hand plan' in payload['native']['note']


def test_hawor_camera_requires_safe_explicit_frame_mapping(fixture):
    value = fixture('hawor_camera', world=False)
    payload = dict(init_root_orient=np.tile(np.eye(3), (1, 3, 1, 1)).tolist(),
        init_trans=np.arange(9).reshape(1, 3, 3).tolist())
    path = value.native / 'input/cam_space/1/0_4.json'; atomic_json(path, payload)
    assert 'safe hand plan' in inspect(value, 2)['native']['reason']
    plan = value.native / 'hawor-plan.json'
    atomic_json(plan, dict(frames=5, chunks=[dict(slot=1, frames=[0, 2, 4])]))
    atomic_json(value.native / 'motion.complete.json', dict(run_id=value.run_id, files={str(path): file_identity(path)}))
    # Legacy hand plans are not identity-frozen: a plausible filename/length
    # must never stand in for the missing trusted row-to-frame mapping.
    legacy = inspect(value, 2)
    assert legacy['native']['available'] is False
    assert 'identity is not frozen' in legacy['native']['reason']
    assert legacy['standard']['available'] is True
    assert legacy['standard']['K'] == value.arrays['K'][2].tolist()
    atomic_json(value.native / 'motion.complete.json', dict(run_id=value.run_id,
        files={str(path): file_identity(path), str(plan): file_identity(plan)}))
    result = inspect(value, 2)
    assert result['native']['available']
    assert by_name(result)['right.init_trans']['value'] == [3, 4, 5]
    assert inspect(value, 1)['native']['available'] is False
    path.write_text(json.dumps(payload) + ' ')
    assert 'identity' in inspect(value, 2)['native']['reason']


def test_hawor_camera_can_use_frozen_frame_indices_inside_json(fixture):
    value = fixture('hawor_camera', world=False)
    path = value.native / 'input/cam_space/0/0_4.json'
    atomic_json(path, dict(frame_indices=[0, 2, 4], init_trans=np.arange(9).reshape(1, 3, 3).tolist()))
    atomic_json(value.native / 'motion.complete.json', dict(run_id=value.run_id, files={str(path): file_identity(path)}))
    result = inspect(value, 4)
    assert result['native']['available'] and by_name(result)['left.init_trans']['value'] == [6, 7, 8]
    assert inspect(value, 1)['native']['available'] is False


@pytest.mark.parametrize('kind', ['object', 'corrupt', 'oversize', 'escape', 'bad_shape'])
def test_native_unsafe_or_malformed_content_is_displayed_as_unavailable(fixture, monkeypatch, kind):
    value = fixture(); path = value.native / 'mint_native.npz'
    if kind == 'object':
        np.savez(path, hand=np.array([object()], dtype=object))
    elif kind == 'corrupt':
        path.write_bytes(b'not an npz')
    elif kind == 'oversize':
        mint(value)
        monkeypatch.setattr('mint.comparison.parameters.MAX_MEMBER_BYTES', 100)
    elif kind == 'escape':
        outside = value.folder.parent.parent / 'outside.npz'
        np.savez(outside, hand=np.zeros((5, 218)), pose_enc=np.zeros((5, 9)))
        path.symlink_to(outside)
    else:
        atomic_npz(path, hand=np.zeros((4, 218)), pose_enc=np.zeros((5, 9)))
    payload = inspect(value)
    assert payload['native']['available'] is False and payload['native']['reason']


def test_cache_reuses_native_members_and_checks_file_identity(fixture, monkeypatch):
    value = fixture(); mint(value)
    first = inspect(value, 0)
    original = np.load
    reads = []
    def load(path, *args, **kwargs):
        if str(path).endswith('mint_native.npz'):
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(np, 'load', load)
    inspect(value, 1)
    assert not reads  # Playback does not reload full-video native members.
    atomic_npz(value.native / 'mint_native.npz', hand=np.zeros((5, 218)), pose_enc=np.zeros((5, 9)))
    current = inspect(value, 0)
    assert reads and first['native'] != current['native']


def test_parameters_http_route_is_read_only_and_validates_frame(fixture, monkeypatch):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    value = fixture(); mint(value)
    monkeypatch.setattr('mint.comparison.api.ComparisonManager', lambda: value.manager)
    app = Flask(__name__); app.register_blueprint(comparison_blueprint())
    client = app.test_client(); url = '/api/comparison/runs/' + value.run_id + '/parameters'
    assert client.get(url + '?frame=2&origin=10').get_json()['source_frame_index'] == 16
    assert client.get(url + '?frame=5').status_code == 400
    assert client.get(url + '?frame=1.2').status_code == 400
    assert client.post(url).status_code == 405
