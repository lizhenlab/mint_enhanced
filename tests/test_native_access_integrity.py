"""Native source readiness and explicit calibration/training boundaries."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from mint.comparison import native_sources as native
from tests.test_comparison import arrays, fake_backend


def write(path, text='fixture'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_catalog_separates_saved_inventory_from_current_format_presence(tmp_path):
    source = tmp_path / 'egodex'
    (source / 'test').mkdir(parents=True)
    empty = tmp_path / 'arctic'; empty.mkdir()
    write(tmp_path / 'DATASET_CATALOG_20261016.json', json.dumps({'entries': [
        {'dataset': 'EgoDex', 'path': str(source)}, {'dataset': 'ARCTIC', 'path': str(empty)}]}))
    result = native.dataset_catalog(tmp_path)
    assert result['inventory_source'] == 'saved_inventory'
    assert result['full_content_verified'] is False
    ego, arctic = result['datasets']
    assert ego['native_import'] and ego['format_ready']
    assert ego['full_content_verified'] is False
    assert arctic['native_import'] and not arctic['format_ready']
    assert 'P1' in arctic['format_issue']
    # Existence in an inventory does not freeze the live availability check.
    (source / 'test').rmdir()
    assert native.dataset_catalog(tmp_path)['datasets'][0]['format_ready'] is False


def test_directory_fallback_still_does_not_certify_contents(tmp_path):
    (tmp_path / 'egodex/part1').mkdir(parents=True)
    result = native.dataset_catalog(tmp_path)
    assert result['inventory_source'] == 'live_directory_listing'
    assert result['datasets'][0]['format_ready']
    assert result['full_content_verified'] is False


def test_arctic_sequence_readiness_reports_missing_gt_and_rgb_before_prepare(tmp_path):
    write(tmp_path / 'data/splits_json/protocol_p1.json', json.dumps({'val': ['s05/ready', 's05/missing']}))
    write(tmp_path / 'data/meta/misc.json', '{}')
    for suffix in ('.mano.npy', '.egocam.dist.npy'):
        write(tmp_path / ('data/raw_seqs/s05/ready' + suffix))
    write(tmp_path / 'data/cropped_images/s05/ready/0/000001.jpg')
    rows = {row['sequence']: row for row in native.sequences('arctic', tmp_path, split='val')['sequences']}
    assert rows['s05/ready']['has_mano']
    assert rows['s05/ready']['reference_available']
    assert rows['s05/ready']['reference_issue'] == ''
    missing = rows['s05/missing']
    assert not missing['has_mano'] and not missing['reference_available']
    assert all(text in missing['reference_issue'] for text in ('MANO', '相机', 'RGB'))
    assert missing['reference_check'] == 'basic_file_presence'


def test_egodex_requires_paired_video_and_preserves_partition_browsing(tmp_path):
    write(tmp_path / 'test/action/0.hdf5')
    write(tmp_path / 'test/action/1.hdf5')
    write(tmp_path / 'test/action/1.mp4')
    result = native.sequences('egodex', tmp_path, group='test/action', split='test')
    assert not result['sequences'][0]['reference_available']
    assert 'MP4' in result['sequences'][0]['reference_issue']
    assert result['sequences'][1]['reference_available']
    with pytest.raises(ValueError, match='official partition'):
        native.sequences('egodex', tmp_path, group='test/action', split='train')


def test_dexycb_requires_joint_labels_metadata_and_camera_calibration(tmp_path):
    sequence = '20201002-subject-08/recording/123456'
    folder = tmp_path / sequence
    write(folder / 'color_000000.jpg')
    result = native.sequences('dexycb', tmp_path, group='20201002-subject-08', split='test')
    row = result['sequences'][0]
    assert not row['reference_available']
    assert all(text in row['reference_issue'] for text in ('左右手', '内参', '关节点'))
    write(folder / 'labels_000000.npz')
    write(folder.parent / 'meta.yml')
    write(tmp_path / 'calibration/intrinsics/123456_640x480.yml')
    assert native.sequences('dexycb', tmp_path, group='20201002-subject-08', split='test')['sequences'][0]['reference_available']


def test_dexycb_partial_labels_keep_valid_later_ranges_selectable(tmp_path):
    sequence = '20201002-subject-08/recording/123456'
    folder = tmp_path / sequence
    write(folder / 'color_000000.jpg')
    write(folder / 'color_000001.jpg')
    write(folder / 'labels_000001.npz')
    write(folder.parent / 'meta.yml')
    write(tmp_path / 'calibration/intrinsics/123456_640x480.yml')
    row = native.sequences('dexycb', tmp_path, group='20201002-subject-08', split='test')['sequences'][0]
    assert row['reference_available']
    assert row['reference_coverage'] == 'unknown_until_preflight'
    selected = native.source_plan('dexycb', tmp_path, sequence, start_frame=1, max_frames=1)
    files = [record['path'] for record in selected['files']]
    assert str(folder / 'labels_000001.npz') in files
    assert str(folder / 'labels_000000.npz') not in files
    with pytest.raises(ValueError, match='labels_000000.npz'):
        native.source_plan('dexycb', tmp_path, sequence)


@pytest.mark.parametrize('calibration', ['false', 'true', 0, 1, None, [], {}])
def test_native_comparison_rejects_truthiness_before_accessing_a_version(monkeypatch, calibration):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    def forbidden_manager():
        pytest.fail('Invalid protocol must fail before reading or preparing a dataset')
    monkeypatch.setattr('mint.comparison.api.ComparisonManager', forbidden_manager)
    app = Flask(__name__); app.register_blueprint(comparison_blueprint())
    response = app.test_client().post('/api/comparison/native/version/compare',
                                      json={'calibration': calibration, 'selections': [{'algorithm': 'mint'}]})
    assert response.status_code == 400
    assert 'explicit boolean' in response.json['error']


@pytest.mark.parametrize('calibration', [True, False, 'omitted'])
def test_native_comparison_only_passes_declared_calibration(monkeypatch, calibration):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    sample = {'id': 'version', 'plan': {'dataset': 'egodex', 'split': 'test'}, 'source': '/prepared/video.mp4',
              'reference': '/prepared/reference.npz', 'frames': 2,
              'reference_identity': {'path': '/prepared/reference.npz'},
              'camera': {'K': np.eye(3).tolist(), 'width': 640, 'height': 480},
              'details': {'label_role': 'official_reference'}}
    calls, validated_references = [], []
    def create(**values):
        calls.append(values)
        return {'id': 'comparison', 'runs': [{'id': 'reference-run'}]}
    manager = SimpleNamespace(create=create, attach=lambda *_: {'id': 'comparison'})
    monkeypatch.setattr('mint.comparison.api.ComparisonManager', lambda: manager)
    monkeypatch.setattr('mint.comparison.api.backend_spec', fake_backend)
    monkeypatch.setattr('mint.comparison.research.ResearchRegistry',
                        lambda _: SimpleNamespace(get=lambda *_: {'name': 'sample', 'native': sample}))
    monkeypatch.setattr(native, 'reuse_native', lambda record: record['native'])
    monkeypatch.setattr('mint.comparison.reference_validity.assert_reference_import_current',
                        lambda _manager, entry: validated_references.append(entry))
    monkeypatch.setattr('mint.comparison.preflight.inspect_request', lambda *_, **__: {'ready': True})
    app = Flask(__name__); app.register_blueprint(comparison_blueprint())
    body = {'selections': [{'algorithm': 'mint'}]}
    if calibration != 'omitted':
        body['calibration'] = calibration
    response = app.test_client().post('/api/comparison/native/version/compare', json=body)
    assert response.status_code == 201
    assert len(calls) == 2
    assert validated_references == [{'path': sample['reference'], 'identity': sample['reference_identity']}]
    assert all(call['camera'] == (sample['camera'] if calibration is True else None) for call in calls)


@pytest.mark.parametrize('axis', [0, 1])
def test_native_fov_training_rejects_off_center_intrinsics_before_publication(tmp_path, monkeypatch, axis):
    value = arrays(2); K = value['K'][0].copy(); K[axis, 2] += .1
    value['K'] = np.broadcast_to(K, (2, 3, 3)).copy()
    monkeypatch.setattr(native, '_ego', lambda *_: (value, K, np.arange(2), 30., (480, 640),
                                                   {'label_role': 'ARKit_tracking_reference'}, None))
    plan = {'files': [], 'implementation': {}, 'dataset': 'egodex', 'training': True, 'split': 'train'}
    with pytest.raises(ValueError, match='centered pinhole'):
        native._prepare_native(plan, tmp_path / 'owned')
    assert not (tmp_path / 'owned/native.json').exists()
    assert not (tmp_path / 'owned/reference.npz').exists()


def test_centered_camera_training_preserves_fov_order_and_no_mano_claim(tmp_path, monkeypatch):
    value = arrays(2); K = value['K'][0].copy(); K[1, 1] = 450
    value['K'] = np.broadcast_to(K, (2, 3, 3)).copy()
    def reader(_plan, folder, *_):
        write(folder / 'video.mp4')
        return value, K, np.arange(2), 30., (480, 640), {'label_role': 'ARKit_tracking_reference'}, None
    captured = {}
    def export(destination, episodes, **kwargs):
        captured.update(episodes[0]['columns']); captured.update(kwargs)
        return {'root': str(destination)}
    monkeypatch.setattr(native, '_ego', reader)
    monkeypatch.setattr('mint.comparison.training_data._write_dataset', export)
    plan = {'files': [], 'implementation': {}, 'dataset': 'egodex', 'training': True, 'split': 'train'}
    result = native._prepare_native(plan, tmp_path / 'owned')
    assert result['supervision'] == ['camera', 'arkit_joints']
    assert captured['hand_supervision'] == 'none'
    np.testing.assert_allclose(captured['cam_fov'][0],
                               [2 * np.arctan(480 / 900), 2 * np.arctan(640 / 1000)])
    assert not captured['hand_kept'].any()

