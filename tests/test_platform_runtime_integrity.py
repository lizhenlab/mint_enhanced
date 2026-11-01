"""Protocol validation and derived-result/render admission regressions."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from mint.comparison.contracts import source_request, validate_geometry, load_result
from mint.comparison.registry import normalize_options
from mint.comparison.results import combine_camera, paired_metrics
from mint.comparison.tools import RenderTools
from mint.comparison.jobs import ComparisonManager
from mint.runtime import atomic_json
from tests.test_comparison import arrays, published, fake_backend


@pytest.mark.parametrize('algorithm,options', [
    ('mint', {'max_gpus': 1.5}), ('mint', {'max_gpus': True}),
    ('hawor_full', {'analysis_width': 832.5}), ('ace_kfree', {'encode_width': 640.5}),
    ('ace_kfree', {'stride': 80.5}), ('hawor_camera', {'focal': True}),
])
def test_numerical_protocol_never_silently_truncates_options(algorithm, options):
    with pytest.raises(ValueError):
        normalize_options(algorithm, options)


@pytest.mark.parametrize('width', [640.5, True, float('inf')])
def test_calibration_grid_must_be_an_actual_integer(tmp_path, width):
    source = tmp_path / 'video.mp4'; source.write_bytes(b'video')
    camera = {'K': [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
              'width': width, 'height': 480, 'source': 'explicit calibration'}
    with pytest.raises(ValueError):
        source_request(source, camera=camera)


def test_unsigned_descending_indices_cannot_bypass_frame_mapping_validation():
    value = arrays(3)
    value['source_frame_indices'] = np.array([12, 11, 10], dtype=np.uint64)
    with pytest.raises(ValueError, match='increasing'):
        validate_geometry(value)


def test_combined_gt_hands_do_not_declare_predicted_world_camera_as_gt(tmp_path):
    hands, camera, target = (tmp_path / name for name in ('hands', 'camera', 'derived'))
    published(hands, reference_role='user_declared_ground_truth'); published(camera)
    combine_camera(hands, camera, target, metadata={'run_id': 'derived', 'label': 'derived'})
    metadata, value = load_result(target)
    assert metadata['reference_role'] == 'derived_camera_combination'
    assert metadata['component_reference_roles']['hands'] == 'user_declared_ground_truth'
    assert paired_metrics(camera, target)['scope'] == 'algorithm_discrepancy_not_accuracy'
    np.testing.assert_array_equal(value['joints_camera_m'], load_result(hands)[1]['joints_camera_m'])


def render_fixture(tmp_path, monkeypatch, value):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    monkeypatch.setattr('mint.comparison.tools.capabilities', lambda: {'mujoco': {'ready': True}})
    manager = ComparisonManager(tmp_path / 'store', autostart=False)
    source = tmp_path / 'video.mp4'; source.write_bytes(b'video')
    comparison = manager.create(source, [{'algorithm': 'mint'}])
    run = comparison['run_ids'][0]
    published(manager.root / 'runs' / run / 'result', value, run_id=run, sample_id=comparison['sample_id'])
    manager._set(run, 'completed')
    atomic_json(manager.root / 'inputs' / comparison['sample_id'] / 'sample.json',
                {'frames': 5, 'source_frame_indices': [10, 11, 12, 13, 14]})
    return manager, RenderTools(manager, autostart=False), run


def test_renderer_rejects_missing_source_frames_before_scheduling(tmp_path, monkeypatch):
    value = arrays(3); value['source_frame_indices'] = np.array([10, 12, 14])
    value['vertices_camera_m'] = np.ones((3, 2, 778, 3))
    manager, tools, run = render_fixture(tmp_path, monkeypatch, value)
    try:
        with pytest.raises(ValueError, match='缺失帧'):
            tools.start(run)
        assert not list(tools.root.glob('*/request.json'))
    finally:
        tools.close(); manager.close()


def test_renderer_checks_world_poses_only_in_requested_interval(tmp_path, monkeypatch):
    value = arrays(); value['vertices_camera_m'] = np.ones((5, 2, 778, 3))
    value['pose_valid'][3] = False; value['T_world_camera'][3] = np.nan
    manager, tools, run = render_fixture(tmp_path, monkeypatch, value)
    try:
        with pytest.raises(ValueError, match='valid camera geometry'):
            tools.start(run, space='world')
        assert tools.start(run, space='world', end=3)['status'] == 'queued'
    finally:
        tools.close(); manager.close()


def test_stale_render_queue_snapshot_cannot_relaunch_a_finished_job(tmp_path, monkeypatch):
    tools = RenderTools(SimpleNamespace(root=tmp_path), autostart=False)
    monkeypatch.setattr(tools, 'status', lambda key: {'id': key, 'status': 'completed'})
    monkeypatch.setattr(tools, '_lease', lambda *args: pytest.fail('Completed render must not acquire GPU'))
    tools._execute({'id': 'a' * 64, 'status': 'queued'}, tmp_path)
    tools.close()
