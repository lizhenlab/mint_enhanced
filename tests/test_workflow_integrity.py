"""Training data semantics and full-state recovery boundaries."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mint.runtime import atomic_json, file_identity
from mint.comparison.training_curves import checkpoint_summary, latest_checkpoint, read_curves
from mint.comparison.research import ResearchRegistry
from mint.comparison.workflows import WorkflowManager
from tests.test_platform_lifecycle import manager
from tests.test_comparison_platform import annotated_episode


def full_checkpoint(root, step=1):
    folder = root / f'step_{step:08d}'
    folder.mkdir(parents=True)
    for name in ('model.safetensors', 'optimizer.bin', 'scheduler.bin', 'random_states_0.pkl'):
        (folder / name).write_bytes(name.encode())
    atomic_json(folder / 'checkpoint.json', {'schema': 1, 'step': step,
        'files': {p.name: file_identity(p) for p in folder.iterdir() if p.is_file()}})
    return folder


def test_checkpoint_curve_does_not_call_model_only_marker_complete(tmp_path):
    training = tmp_path / 'job' / 'training'
    valid = full_checkpoint(training)
    invalid = training / 'step_00000002'; invalid.mkdir()
    (invalid / 'model.safetensors').write_bytes(b'model without resumable state')
    atomic_json(invalid / 'checkpoint.json', {'schema': 1, 'step': 2,
        'files': {'model.safetensors': file_identity(invalid / 'model.safetensors')}})
    corrupt = training / 'step_00000003'; corrupt.mkdir()
    (corrupt / 'checkpoint.json').write_text('{incomplete')
    result = read_curves(SimpleNamespace(root=tmp_path, get=lambda _: {'status': 'interrupted'}), 'job')
    assert [v['complete'] for v in result['checkpoints']] == [True, False, False]
    assert checkpoint_summary(valid)['complete']
    with pytest.raises(ValueError, match='Latest training checkpoint'):
        latest_checkpoint(training)


def test_checkpoint_identity_and_step_are_required_for_recovery(tmp_path):
    first = full_checkpoint(tmp_path, 9)
    second = full_checkpoint(tmp_path, 10)
    assert latest_checkpoint(tmp_path) == str(second)
    (second / 'optimizer.bin').write_bytes(b'changed optimizer')
    assert not checkpoint_summary(second)['complete']
    assert 'changed' in checkpoint_summary(second)['error']
    marker = json.loads((first / 'checkpoint.json').read_text())
    marker['step'] = 10
    atomic_json(first / 'checkpoint.json', marker)
    assert not checkpoint_summary(first)['complete']


def test_curve_reader_ignores_structurally_invalid_rows_and_nonfinite_metrics(tmp_path):
    folder = tmp_path / 'job'; folder.mkdir()
    rows = [
        {'step': 1, 'attempt': '1', 'metrics': {'loss': 3}},
        {'step': '2', 'attempt': '1', 'metrics': {'loss': 2}},
        {'step': True, 'attempt': '1', 'metrics': {'loss': 2}},
        {'step': 2, 'attempt': ['broken'], 'metrics': {}},
        {'step': 2, 'attempt': '2', 'metrics': {'loss': 1, 'bad': float('inf')}},
    ]
    (folder / 'metrics.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    result = read_curves(SimpleNamespace(root=tmp_path, get=lambda _: {'status': 'running'}), 'job')
    assert [v['step'] for v in result['points']] == [1, 2]
    assert result['points'][1]['metrics'] == {'loss': 1}


@pytest.mark.parametrize('parameters', [
    {'episodes': [0], 'max_frames': 2.5},
    {'episodes': [0], 'max_frames': True},
    {'episodes': [0, 0]},
    {'episodes': [0.5]},
    {'episodes': [True]},
])
def test_conversion_rejects_silent_frame_truncation_and_duplicate_episodes(manager, tmp_path, monkeypatch, parameters):
    import mint.comparison.datasets as datasets
    monkeypatch.setattr(datasets, 'dataset_info', lambda _: (tmp_path, {'fps': 30}, ['ego']))
    monkeypatch.setattr(datasets, 'select_episode', lambda _: (tmp_path, {}, 'ego', {}, {'camera': True}))
    monkeypatch.setattr(datasets, 'annotation_identity', lambda *args: {})
    workflows = WorkflowManager(manager, ResearchRegistry(manager), autostart=False)
    try:
        with pytest.raises(ValueError, match='integer'):
            workflows.preflight('prepare_training', {'source': str(tmp_path), **parameters})
    finally:
        workflows.close()


def test_reviewed_camera_export_keeps_canonical_vertical_horizontal_fov(tmp_path, monkeypatch):
    import mint.comparison.training_data as data
    import mint.comparison.contracts as contracts
    import mint.comparison.prepare as prepare
    import mint.visualization.draw as draw
    import mint.visualization.encoding as encoding
    run = 'fixture'; result = tmp_path / 'runs' / run / 'result'; result.mkdir(parents=True)
    atomic_json(result / 'result.json', {'fixture': True})
    frame_path = tmp_path / 'frames.npy'
    np.save(frame_path, np.zeros((2, 360, 640, 3), np.uint8))
    sample = {'width': 640, 'height': 360, 'fps': 30, 'frames_path': str(frame_path), 'source_frame_indices': [5, 6]}
    K = np.tile([[500., 0, 320], [0, 400., 180], [0, 0, 1]], (2, 1, 1))
    arrays = {'T_world_camera': np.tile(np.eye(4), (2, 1, 1)), 'pose_valid': np.ones(2, bool),
              'source_frame_indices': np.array([5, 6]), 'K': K}
    monkeypatch.setattr(contracts, 'load_result', lambda _: ({}, arrays))
    monkeypatch.setattr(prepare, 'prepare_sample', lambda *a, **kw: sample)
    monkeypatch.setattr(encoding, 'atomic_video_file', lambda path, render, count: render(path))
    monkeypatch.setattr(draw, 'H264PipeWriter', lambda *a, **kw: SimpleNamespace(write=lambda frame: None, close=lambda: None))
    monkeypatch.setattr(data, '_write_dataset', lambda destination, episodes, **kw: {'episodes': episodes})
    source = {'sample_id': 'sample', 'sample_request': {}}
    manager = SimpleNamespace(root=tmp_path, row=lambda _: {'payload': json.dumps(source)})
    dataset = {'id': 'reviewed', 'entries': [{'run_id': run, 'start': 0, 'end': 2,
        'result_identity': file_identity(result / 'result.json')}]}
    output = data.export_reviewed_camera_dataset(manager, dataset, tmp_path / 'converted')
    fov = np.asarray(output['episodes'][0]['columns']['cam_fov'])
    expected = np.tile([2 * np.arctan(360 / 800), 2 * np.arctan(640 / 1000)], (2, 1))
    np.testing.assert_allclose(fov, expected)
    # Reconstruct the original non-square camera using the training convention.
    np.testing.assert_allclose(360 / (2 * np.tan(fov[:, 0] / 2)), K[:, 1, 1])
    np.testing.assert_allclose(640 / (2 * np.tan(fov[:, 1] / 2)), K[:, 0, 0])


@pytest.mark.parametrize('origin', [
    {'type': 'human_reviewed_pseudo_camera_labels'},
    {'type': 'existing_declared_camera_annotations', 'label_role': 'model_generated_pseudo_labels'},
])
def test_camera_only_pseudo_labels_cannot_bypass_training_opt_in(manager, tmp_path, monkeypatch, origin):
    import sys
    root = tmp_path / 'camera-pseudo'; (root / 'meta').mkdir(parents=True)
    atomic_json(root / 'meta/info.json', {'hand_supervision': 'none'})
    atomic_json(root / 'source_manifest.json', {'label_origin': origin, 'cam_fov_order': 'height_width'})
    config = tmp_path / 'train.yaml'
    config.write_text('data:\n  root: ' + str(root) + '\nmodel: {}\ntrain: {}\n')
    monkeypatch.setattr('mint.comparison.workflows.owned_environment', lambda _: Path(sys.executable))
    monkeypatch.setattr('mint.comparison.workflows.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"missing":[],"versions":{}}'))
    workflows = WorkflowManager(manager, ResearchRegistry(manager), autostart=False)
    try:
        denied = workflows.preflight('train', {'config': str(config), 'allow_pseudo_labels': False})
        assert not denied['ready'] and any('allow_pseudo_labels' in error for error in denied['errors'])
        allowed = workflows.preflight('train', {'config': str(config), 'allow_pseudo_labels': True})
        assert allowed['ready'], allowed['errors']
    finally:
        workflows.close()


def test_old_reviewed_fov_requires_regeneration_even_through_conversion(manager, tmp_path, monkeypatch):
    import sys
    from mint.comparison.training_data import camera_fov_schema_errors, prepare_existing_camera_dataset
    old = tmp_path / 'old-reviewed'; (old / 'meta').mkdir(parents=True)
    atomic_json(old / 'meta/info.json', {'hand_supervision': 'none'})
    atomic_json(old / 'source_manifest.json', {'label_origin': {'type': 'human_reviewed_pseudo_camera_labels'}})
    converted = tmp_path / 'old-converted'; (converted / 'meta').mkdir(parents=True)
    atomic_json(converted / 'meta/info.json', {'hand_supervision': 'none'})
    atomic_json(converted / 'source_manifest.json', {'label_origin': {
        'type': 'existing_declared_camera_annotations', 'source': str(old)}})
    assert camera_fov_schema_errors(converted)
    with pytest.raises(ValueError, match='FoV'):
        prepare_existing_camera_dataset(converted, tmp_path / 'another-conversion')
    config = tmp_path / 'train.yaml'
    config.write_text('data:\n  root: ' + str(converted) + '\nmodel: {}\ntrain: {}\n')
    monkeypatch.setattr('mint.comparison.workflows.owned_environment', lambda _: Path(sys.executable))
    monkeypatch.setattr('mint.comparison.workflows.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"missing":[],"versions":{}}'))
    workflows = WorkflowManager(manager, ResearchRegistry(manager), autostart=False)
    try:
        denied = workflows.preflight('train', {'config': str(config), 'allow_pseudo_labels': True})
        assert not denied['ready'] and any('FoV' in error for error in denied['errors'])
    finally:
        workflows.close()


def test_legacy_horizontal_vertical_fov_is_normalized_in_training_conversion(annotated_episode, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from mint.comparison.training_data import prepare_existing_camera_dataset
    root, episode, _ = annotated_episode
    table = pq.read_table(episode['parquet']).drop(['cam_trans', 'cam_quat', 'cam_fov'])
    table = table.append_column('extrinsics_w2c', pa.array(np.tile(np.eye(4).reshape(1, 16), (10, 1)).tolist()))
    table = table.append_column('fov', pa.array([[1.2, .7]] * 10))
    pq.write_table(table, episode['parquet'])
    output = prepare_existing_camera_dataset(root, tmp_path / 'converted', episode_ids=[2])
    converted = pq.read_table(Path(output['root']) / 'data/chunk-000/file-000.parquet')
    np.testing.assert_allclose(converted['cam_fov'].to_pylist(), [[.7, 1.2]] * 4)
    info = json.loads((Path(output['root']) / 'meta/info.json').read_text())
    assert info['cam_fov_order'] == output['provenance']['cam_fov_order'] == 'height_width'
