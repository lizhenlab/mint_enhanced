"""Regression coverage for frame provenance, calibration and failed evaluations."""
import json
from pathlib import Path

import numpy as np
import pytest

from mint.runtime import atomic_json, atomic_npz, digest, file_identity
from mint.comparison.datasets import annotated_intrinsics, resolve_dataset_input
from mint.comparison.evaluation import collection_report, report_csv
from mint.comparison.native_sources import prepare_native, source_plan
from tests.test_comparison_platform import annotated_episode
from tests.test_platform_lifecycle import manager, comparison


def _rewrite_column(path, name, values):
    import pyarrow as pa
    import pyarrow.parquet as pq
    table = pq.read_table(path)
    table = table.set_column(table.schema.get_field_index(name), name, pa.array(values))
    pq.write_table(table, path)


def test_k_given_rejects_time_varying_calibration_only_in_selected_frames(annotated_episode):
    root, ep, K = annotated_episode
    values = np.broadcast_to(K, (10, 3, 3)).copy()
    values[6, 0, 0] += 5
    _rewrite_column(ep['parquet'], 'intrinsics', values.reshape(10, 9).tolist())
    selection = {'root': str(root), 'episode': 2, 'use_calibration': True}
    with pytest.raises(ValueError, match='static intrinsics'):
        resolve_dataset_input(selection, target_fps=15)
    resolved, _ = resolve_dataset_input({**selection, 'use_calibration': False}, target_fps=15)
    assert resolved['camera'] is None
    # A one-frame range is a valid static-K input, even when other frames vary.
    resolved, _ = resolve_dataset_input(selection, target_fps=15, max_frames=1)
    np.testing.assert_allclose(resolved['camera']['K'], K)


def test_reference_fov_fallback_keeps_each_selected_frame_intrinsics(annotated_episode):
    import pyarrow.parquet as pq
    root, ep, fallback = annotated_episode
    table = pq.read_table(ep['parquet']).drop_columns(['intrinsics'])
    pq.write_table(table, ep['parquet'])
    fov = np.ones((10, 2)); fov[4] = [.8, 1.2]; fov[6] = [1.1, .9]
    _rewrite_column(ep['parquet'], 'cam_fov', fov.tolist())
    K, source = annotated_intrinsics(ep, [1, 3], fallback, image_hw=(64, 128))
    np.testing.assert_allclose(K[:, 0, 0], 128 / (2 * np.tan(fov[[4, 6], 1] / 2)))
    np.testing.assert_allclose(K[:, 1, 1], 64 / (2 * np.tan(fov[[4, 6], 0] / 2)))
    assert 'per-frame FoV' in source and not np.array_equal(K[0], K[1])
    with pytest.raises(ValueError, match='static intrinsics'):
        resolve_dataset_input({'root': str(root), 'episode': 2, 'use_calibration': True}, target_fps=15)


def test_legacy_fov_order_matches_horizontal_vertical_export(annotated_episode):
    import pyarrow as pa
    import pyarrow.parquet as pq
    _, ep, fallback = annotated_episode
    table = pq.read_table(ep['parquet']).drop_columns(['intrinsics', 'cam_fov'])
    table = table.append_column('fov', pa.array([[1.2, .8]] * 10))
    pq.write_table(table, ep['parquet'])
    K, _ = annotated_intrinsics(ep, [1], fallback, image_hw=(64, 128))
    assert K[0, 0, 0] == pytest.approx(128 / (2 * np.tan(1.2 / 2)))
    assert K[0, 1, 1] == pytest.approx(64 / (2 * np.tan(.8 / 2)))


@pytest.mark.parametrize('positions', [[-1], [4], [True], [.5], [float('nan')]])
def test_annotation_positions_cannot_wrap_or_escape_episode(annotated_episode, positions):
    _, ep, K = annotated_episode
    with pytest.raises(ValueError, match='inside the selected Episode'):
        annotated_intrinsics(ep, positions, K, image_hw=(64, 128))


@pytest.mark.parametrize('training', [0, 1, 'false', None])
def test_native_training_flag_is_not_truthiness_coerced(tmp_path, training):
    with pytest.raises(ValueError, match='explicit boolean'):
        source_plan('egodex', tmp_path, 'part1/action/0', split='train', training=training)


@pytest.mark.parametrize('legacy', [False, True])
def test_published_native_frame_map_is_checked_without_rewriting_version(tmp_path, legacy):
    folder = tmp_path / 'owned' / 'dataset'; folder.mkdir(parents=True)
    plan = {'files': []}
    video = folder / 'video.mp4'; video.write_bytes(b'immutable video fixture')
    reference = folder / 'reference.npz'; atomic_npz(reference, value=np.arange(3))
    path = folder / 'native-frame-map.npz'
    ids = np.arange(3) + 20
    atomic_npz(path, native_frame_indices=ids, original_timestamps_s=ids / 30,
               sample_timestamps_s=np.arange(3) / 30)
    value = {'plan': plan, 'video_identity': file_identity(video), 'reference_identity': file_identity(reference),
             'native_frame_indices': ids.tolist(), 'frames': 3, 'fps': 30}
    if not legacy:
        value['frame_map_identity'] = file_identity(path)
    marker = folder / 'native.json'; atomic_json(marker, value)
    before = file_identity(marker)
    assert prepare_native(plan, folder)['frames'] == 3
    assert file_identity(marker) == before
    atomic_npz(path, native_frame_indices=ids + 1, original_timestamps_s=ids / 30,
               sample_timestamps_s=np.arange(3) / 30)
    with pytest.raises(ValueError, match='frame map changed'):
        prepare_native(plan, folder)


def _evaluation(items, selections=None):
    selections = selections or [{'algorithm': 'mint'}]
    return {'id': 'evaluation', 'status': 'partial', 'selections': selections, 'options': {},
            'backend_versions': {s['algorithm'] + ':' + digest(s): s['algorithm'] + '-v1' for s in selections},
            'inputs': [{'source': '/fixture/video.mp4'} for _ in items], 'items': items}


def test_evaluation_keeps_all_selected_models_when_children_cannot_be_created(manager):
    report = collection_report(manager, _evaluation([{'status': 'failed', 'error': 'input preparation failed'}],
                                                  [{'algorithm': 'mint'}, {'algorithm': 'hawor_full'}]))
    assert len(report['rows']) == 2 and report['scorable_sequences'] == 0
    for row in report['rows']:
        assert row['attempted_sequences'] == row['failed_sequences'] == row['unscorable_sequences'] == 1
        assert row['scored_sequences'] == 0 and row['gt_geometry_coverage'] is None


def test_missing_reference_does_not_silently_shrink_overall_coverage(manager, tmp_path):
    comp = comparison(manager, tmp_path, reference=True)
    report = collection_report(manager, _evaluation([{'status': 'completed', 'comparison_id': comp['id']},
                                                    {'status': 'failed', 'error': 'reference import failed'}]))
    row = report['rows'][0]
    assert row['attempted_sequences'] == 2 and row['scored_sequences'] == row['failed_sequences'] == 1
    assert row['unscorable_sequences'] == 1 and row['gt_geometry_coverage'] is None
    assert row['available_reference_geometry_coverage'] == 1
    assert 'unscorable_sequences' in report_csv(report)


def test_missing_selected_backend_still_counts_available_reference_denominator(manager, tmp_path):
    comp = comparison(manager, tmp_path, reference=True)
    report = collection_report(manager, _evaluation([{'status': 'partial', 'comparison_id': comp['id']}],
                                                  [{'algorithm': 'hawor_full'}]))
    row = report['rows'][0]
    assert row['attempted_sequences'] == row['failed_sequences'] == 1
    assert row['gt_hand_slots'] == 10 and row['unscorable_sequences'] == 0
    assert row['gt_geometry_coverage'] == 0

