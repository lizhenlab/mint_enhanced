"""Render admission, immutable timelines and owned task publication guards."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mint.comparison import tools as module
from mint.comparison.contracts import load_result
from mint.comparison.tool_worker import render_inputs
from mint.comparison.tools import RenderTools, write_status
from mint.runtime import atomic_json, file_identity
from tests.test_comparison import arrays, sample
from tests.test_platform_runtime_integrity import render_fixture


@pytest.fixture
def rendering(tmp_path, monkeypatch):
    value = arrays()
    value['vertices_camera_m'] = np.ones((5, 2, 778, 3), np.float32)
    manager, service, run = render_fixture(tmp_path, monkeypatch, value)
    source = manager.run(run, summary=True)
    manifest = manager.root / 'inputs' / source['sample_id'] / 'sample.json'
    atomic_json(manifest, {**sample(), 'id': source['sample_id']})
    try:
        yield manager, service, run, manifest
    finally:
        service.close(); manager.close()


def test_options_report_sample_position_ranges_for_sparse_partial_result(tmp_path, monkeypatch):
    value = arrays(4)
    value['source_frame_indices'] = np.array([10, 12, 13, 14])
    value['timestamps_s'] = np.array(sample()['timestamps_s'])[[0, 2, 3, 4]]
    value['pose_valid'][2] = False; value['T_world_camera'][2] = np.nan
    manager, service, run = render_fixture(tmp_path, monkeypatch, value)
    try:
        manifest = manager.root / 'inputs' / manager.run(run)['sample_id'] / 'sample.json'
        atomic_json(manifest, {**sample(), 'id': manager.run(run)['sample_id']})
        info = service.options(run)
        assert info['sample_frames'] == 5 and info['result_frames'] == 4
        assert info['available_ranges'] == [[0, 1], [2, 5]]
        assert info['world_ranges'] == [[0, 1], [2, 3], [4, 5]]
        assert info['world_available'] and not info['mesh_available']
        assert not list(service.root.glob('*/request.json'))
    finally:
        service.close(); manager.close()


def test_camera_only_options_never_advertise_world_trajectory(tmp_path, monkeypatch):
    manager, service, run = render_fixture(tmp_path, monkeypatch, arrays(world=False))
    try:
        info = service.options(run)
        assert not info['world_available'] and info['world_ranges'] == []
        assert info['available_ranges'] == [[0, 5]]
    finally:
        service.close(); manager.close()


def test_render_request_freezes_sample_manifest_and_uses_it_in_cache_identity(rendering):
    manager, service, run, manifest = rendering
    first = service.start(run)
    assert first['sample'] == file_identity(manifest)
    assert first['sample_id'] == manager.run(run)['sample_id']
    changed = json.loads(manifest.read_text()); changed['fps'] = 25
    atomic_json(manifest, changed)
    second = service.start(run)
    assert second['id'] != first['id']
    with pytest.raises(ValueError, match='Input sample changed'):
        render_inputs(first)


def test_same_source_indices_with_different_sample_times_are_rejected(rendering):
    manager, service, run, manifest = rendering
    changed = json.loads(manifest.read_text()); changed['timestamps_s'] = [time + .2 for time in changed['timestamps_s']]
    atomic_json(manifest, changed)
    with pytest.raises(ValueError, match='timestamps'):
        service.options(run)
    with pytest.raises(ValueError, match='timestamps'):
        service.start(run)
    assert not list(service.root.glob('*/request.json'))


@pytest.mark.parametrize('field,value', [('start', True), ('start', 1.5), ('end', float('nan')),
    ('width', float('inf')), ('width', 641), ('width', None), ('include_absent', 1)])
def test_render_parameters_are_not_truncated_or_coerced(rendering, field, value):
    manager, service, run, manifest = rendering
    with pytest.raises(ValueError):
        service.start(run, **{field: value})
    assert not list(service.root.glob('*/request.json'))


def test_worker_cannot_read_replaced_sample_id(rendering):
    manager, service, run, manifest = rendering
    request = service.start(run)
    changed = json.loads(manifest.read_text()); changed['id'] = 'different-sample'
    atomic_json(manifest, changed)
    # Legacy requests lack a sample-file identity but must still match sample IDs.
    request.pop('sample')
    with pytest.raises(ValueError, match='do not match'):
        render_inputs(request)


def test_completed_job_requires_both_video_and_mapping(rendering):
    manager, service, run, manifest = rendering
    job = service.start(run); folder = service.root / job['id']
    (folder / 'video.mp4').write_bytes(b'video')
    atomic_json(folder / 'status.json', {'status': 'completed'})
    assert service.status(job['id'])['status'] == 'evicted'
    atomic_json(folder / 'mapping.json', {'source_frame_indices': list(range(10, 15))})
    assert service.status(job['id'])['status'] == 'completed'
    atomic_json(folder / 'status.json', {'status': 'completed', 'outputs': {
        'video': file_identity(folder / 'video.mp4'), 'mapping': file_identity(folder / 'mapping.json')}})
    (folder / 'video.mp4').write_bytes(b'replaced video')
    assert service.status(job['id'])['status'] == 'evicted'


def test_completed_legacy_job_with_invalid_mapping_cannot_be_downloaded(rendering):
    manager, service, run, manifest = rendering
    job = service.start(run); folder = service.root / job['id']
    (folder / 'video.mp4').write_bytes(b'video')
    (folder / 'mapping.json').write_text('broken mapping')
    atomic_json(folder / 'status.json', {'status': 'completed'})
    assert service.status(job['id'])['status'] == 'evicted'


def test_cancelled_job_cannot_be_overwritten_by_late_wait_or_completion(rendering):
    manager, service, run, manifest = rendering
    job = service.start(run); folder = service.root / job['id']
    assert service.cancel(job['id'])['status'] == 'cancelled'
    for state in ('waiting_gpu', 'running', 'completed'):
        with pytest.raises(InterruptedError):
            write_status(folder, {'id': job['id'], 'status': state})
        assert service.status(job['id'])['status'] == 'cancelled'


def test_gpu_wait_reports_reason_without_starting_renderer(rendering, monkeypatch):
    manager, service, run, manifest = rendering
    job = service.start(run); folder = service.root / job['id']
    monkeypatch.setattr(service, 'probe', lambda: {})
    def stop_wait(_seconds):
        assert service.status(job['id'])['status'] == 'waiting_gpu'
        assert 'GPU' in service.status(job['id'])['waiting_reason']
        service.cancel(job['id'])
    monkeypatch.setattr(service.stop, 'wait', stop_wait)
    with pytest.raises(InterruptedError):
        service._lease(job, folder)
    assert service.status(job['id'])['status'] == 'cancelled'


def test_scheduler_does_not_turn_cancelled_gpu_wait_into_failed(rendering, monkeypatch):
    manager, service, run, manifest = rendering
    job = service.start(run); folder = service.root / job['id']
    ticks = iter((False, True))
    monkeypatch.setattr(service.stop, 'wait', lambda _seconds: next(ticks))
    def cancelled_execution(*args):
        (folder / 'cancel').touch()
        raise InterruptedError('Cancelled while waiting for GPU')
    monkeypatch.setattr(service, '_execute', cancelled_execution)
    service._loop()
    assert service.status(job['id'])['status'] == 'cancelled'


def test_cleanup_lock_blocks_resume_until_previous_execution_finishes(rendering):
    from mint.inference.cache_budget import file_lock
    manager, service, run, manifest = rendering
    job = service.start(run); folder = service.root / job['id']
    service.cancel(job['id'])
    with file_lock(folder / '.execution.lock'):
        with pytest.raises(ValueError, match='cleaning up'):
            service.start(run)
    assert service.start(run)['status'] == 'queued'


def test_invalid_cancel_id_never_creates_a_lock_outside_tool_root(tmp_path):
    service = RenderTools(SimpleNamespace(root=tmp_path), autostart=False)
    try:
        with pytest.raises(KeyError):
            service.cancel('../outside')
        assert not (tmp_path / 'outside').exists()
    finally:
        service.close()


def test_capability_checks_right_hand_assets_and_retarget_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'PROJECT', tmp_path)
    monkeypatch.setattr(module.shutil, 'which', lambda name: '/bin/' + name)
    monkeypatch.setattr(module.importlib.util, 'find_spec', lambda name: object() if name != 'pinocchio' else None)
    robot = tmp_path / 'eval/simulate/wuji-retargeting'
    files = [robot / 'wuji_retargeting/__init__.py']
    files += [robot / f'example/config/adaptive_analytical_wuji_glove_{side}.yaml' for side in ('left', 'right')]
    files += [robot / f'wuji_retargeting/wuji-description/hand/body/{kind}/{side}.{ext}'
              for side in ('left', 'right') for kind, ext in (('urdf', 'urdf'), ('mjcf', 'xml'))]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text('asset')
    for side in ('left', 'right'):
        xml = robot / f'wuji_retargeting/wuji-description/hand/body/mjcf/{side}.xml'
        xml.with_suffix('.STL').write_bytes(b'mesh')
        xml.write_text(f'<mujoco><asset><mesh file="{side}.STL"/></asset></mujoco>')
    info = module.capabilities()['retarget']
    assert not info['ready'] and 'pinocchio' in info['missing']
    monkeypatch.setattr(module.importlib.util, 'find_spec', lambda name: object())
    assert module.capabilities()['retarget']['ready']
    files[-1].unlink()
    assert not module.capabilities()['retarget']['ready']


def test_worker_publication_waits_for_initial_controller_state(tmp_path):
    from mint.inference.cache_budget import file_lock
    finished = threading.Event()
    def publish():
        write_status(tmp_path, {'status': 'completed'})
        finished.set()
    with file_lock(tmp_path / '.state.lock'):
        worker = threading.Thread(target=publish); worker.start()
        assert not finished.wait(.03)
        atomic_json(tmp_path / 'status.json', {'status': 'running'})
    worker.join(timeout=2)
    assert finished.is_set()
    assert json.loads((tmp_path / 'status.json').read_text())['status'] == 'completed'


def test_tool_options_api_reads_existing_result_without_scheduling(rendering, monkeypatch):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    manager, service, run, manifest = rendering
    monkeypatch.setattr('mint.comparison.api.ComparisonManager', lambda: manager)
    monkeypatch.setattr(module, 'RenderTools', lambda manager: service)
    app = Flask(__name__); app.register_blueprint(comparison_blueprint())
    response = app.test_client().get(f'/api/comparison/runs/{run}/tools/options')
    assert response.status_code == 200
    assert response.json['run_id'] == run and response.json['sample_frames'] == 5
    assert not list(service.root.glob('*/request.json'))
    response = app.test_client().post(f'/api/comparison/runs/{run}/tools', json={'unknown': True})
    assert response.status_code == 400 and not list(service.root.glob('*/request.json'))
