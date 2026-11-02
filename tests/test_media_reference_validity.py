"""Withdrawal invalidates cached media without deleting immutable output files."""
import pytest

from mint.comparison.exports import ComparisonExports
from mint.comparison.research import ResearchRegistry
from mint.comparison.tools import RenderTools
from mint.runtime import atomic_json, file_identity
from tests.test_platform_lifecycle import manager
from tests.test_reference_withdrawal import _with_reference, _withdraw


@pytest.mark.parametrize('baseline_only', [False, True])
def test_completed_comparison_video_cannot_expose_withdrawn_gt(manager, tmp_path, baseline_only):
    comp, reference = _with_reference(manager, tmp_path)
    atomic_json(manager.root / 'inputs' / comp['sample_id'] / 'sample.json', {'frames': 5, 'id': comp['sample_id']})
    exports = ComparisonExports(manager, autostart=False)
    try:
        options = {'run_ids': [comp['run_ids'][0]], 'baseline_run': reference} if baseline_only else {}
        job = exports.start(comp['id'], **options)
        video = exports.directory / (job['id'] + '.mp4'); video.write_bytes(b'cached media fixture')
        exports._write(job['id'], status='completed')
        assert exports.status(job['id'])['status'] == 'completed'
        before = file_identity(video)
        _withdraw(manager, reference)
        state = exports.status(job['id'])
        assert state['status'] == 'failed' and state['source_invalidated'] and state['url'] is None
        with pytest.raises(ValueError, match='withdrawn'):
            exports.resume(job['id'])
        assert file_identity(video) == before
    finally:
        exports.close()


def test_queued_video_rejects_withdrawal_before_preparing_or_encoding(manager, tmp_path, monkeypatch):
    comp, reference = _with_reference(manager, tmp_path)
    atomic_json(manager.root / 'inputs' / comp['sample_id'] / 'sample.json', {'frames': 5, 'id': comp['sample_id']})
    exports = ComparisonExports(manager, autostart=False)
    try:
        job = exports.start(comp['id'])
        _withdraw(manager, reference)
        monkeypatch.setattr(exports, '_render', lambda *args, **kwargs: pytest.fail('Withdrawn source must not encode'))
        exports._execute(job['id'])
        state = exports.status(job['id'])
        assert state['status'] == 'failed' and state['source_invalidated']
        with pytest.raises(ValueError, match='withdrawn'):
            exports.resume(job['id'])
        assert not (exports.directory / (job['id'] + '.mp4')).exists()
    finally:
        exports.close()


@pytest.mark.parametrize('status', ['queued', 'completed'])
def test_render_tool_does_not_download_or_acquire_gpu_for_withdrawn_source(manager, tmp_path, monkeypatch, status):
    comp, reference = _with_reference(manager, tmp_path)
    tools = RenderTools(manager, autostart=False)
    key = 'a' * 64; folder = tools.root / key; folder.mkdir()
    atomic_json(folder / 'request.json', {'id': key, 'run_id': reference, 'start': 0, 'end': 5})
    atomic_json(folder / 'status.json', {'status': status})
    (folder / 'video.mp4').write_bytes(b'old media')
    atomic_json(folder / 'mapping.json', {'source_frame_indices': list(range(5))})
    assert tools.status(key)['status'] == status
    before = {p: file_identity(p) for p in folder.iterdir()}
    _withdraw(manager, reference)
    monkeypatch.setattr(tools, '_lease', lambda *args: pytest.fail('Withdrawn source must not acquire GPU'))
    try:
        tools._execute({'id': key, 'status': 'queued'}, folder)
        state = tools.status(key)
        assert state['status'] == 'failed' and state['url'] is None and state['source_invalidated']
        assert {p: file_identity(p) for p in folder.iterdir()} == before
    finally:
        tools.close()


@pytest.mark.parametrize('withdrawal_during', ['waiting', 'probe'])
def test_render_waiter_rechecks_source_before_acquiring_gpu(manager, tmp_path, monkeypatch, withdrawal_during):
    from types import SimpleNamespace
    _, reference = _with_reference(manager, tmp_path)
    tools = RenderTools(manager, autostart=False)
    key = 'b' * 64; folder = tools.root / key; folder.mkdir()
    request = {'id': key, 'run_id': reference, 'start': 0, 'end': 5, 'resource_policy': 'shared'}
    atomic_json(folder / 'request.json', request)
    atomic_json(folder / 'status.json', {'status': 'queued'})
    monkeypatch.delenv('MUJOCO_GL', raising=False)
    calls = []
    def probe():
        calls.append('probe')
        if withdrawal_during == 'probe':
            _withdraw(manager, reference)
            return {'gpu': SimpleNamespace(free_mib=4096, utilization=0, foreign_pids=[])}
        return {}
    def wait(seconds):
        _withdraw(manager, reference)
        return False
    monkeypatch.setattr(tools, 'probe', probe)
    monkeypatch.setattr(tools.stop, 'wait', wait)
    monkeypatch.setattr('mint.comparison.tools.GpuLease', lambda *a: pytest.fail('Withdrawn waiter must not acquire a GPU'))
    monkeypatch.setattr('mint.comparison.tools.subprocess.Popen', lambda *a, **k: pytest.fail('Withdrawn waiter must not launch a renderer'))
    try:
        with pytest.raises(ValueError, match='mean pose'):
            tools._execute(request, folder)
        assert calls == ['probe']
        assert tools.status(key)['source_invalidated']
    finally:
        tools.close()


@pytest.mark.parametrize('withdrawal_during', ['lease', 'device_lookup'])
def test_render_rechecks_source_after_leasing_and_before_launch(manager, tmp_path, monkeypatch, withdrawal_during):
    from types import SimpleNamespace
    _, reference = _with_reference(manager, tmp_path)
    tools = RenderTools(manager, autostart=False)
    key = 'c' * 64; folder = tools.root / key; folder.mkdir()
    request = {'id': key, 'run_id': reference, 'start': 0, 'end': 5, 'resource_policy': 'shared'}
    atomic_json(folder / 'request.json', request)
    atomic_json(folder / 'status.json', {'status': 'queued'})
    monkeypatch.delenv('MUJOCO_GL', raising=False)
    released = []
    lease = SimpleNamespace(release=lambda: released.append(True))
    def acquire(*args):
        if withdrawal_during == 'lease':
            _withdraw(manager, reference)
        return 'gpu', lease, False
    def lookup(*args, **kwargs):
        assert withdrawal_during == 'device_lookup'
        _withdraw(manager, reference)
        return '0, gpu\n'
    monkeypatch.setattr(tools, '_lease', acquire)
    monkeypatch.setattr('mint.comparison.tools.subprocess.check_output', lookup)
    monkeypatch.setattr('mint.comparison.tools.subprocess.Popen', lambda *a, **k: pytest.fail('Withdrawn source must not launch a renderer'))
    try:
        with pytest.raises(ValueError, match='mean pose'):
            tools._execute(request, folder)
        assert released == [True]
        assert tools.status(key)['source_invalidated']
    finally:
        tools.close()


def test_render_launcher_exit_cleans_owned_session_and_preserves_external_session(manager, tmp_path, monkeypatch):
    import json
    import os
    from pathlib import Path
    import signal
    import subprocess
    import sys
    import time
    from mint.inference.cache_budget import _token
    if not Path('/proc/self/stat').exists() or not hasattr(os, 'WNOWAIT'):
        pytest.skip('Unreaped session ownership requires Linux /proc and WNOWAIT')
    _, reference = _with_reference(manager, tmp_path)
    tools = RenderTools(manager, autostart=False)
    key = 'd' * 64; folder = tools.root / key; folder.mkdir()
    request = {'id': key, 'run_id': reference, 'start': 0, 'end': 5, 'resource_policy': 'shared'}
    atomic_json(folder / 'request.json', request)
    atomic_json(folder / 'status.json', {'status': 'queued'})
    monkeypatch.setenv('MUJOCO_GL', 'osmesa')
    original_popen = subprocess.Popen
    external = original_popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
    worker_info = tmp_path / 'owned-worker.json'
    launchers = []
    launcher_code = '''import json, subprocess, sys
from pathlib import Path
worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', sys.argv[1]])
token = Path('/proc/' + str(worker.pid) + '/stat').read_text().rsplit(')', 1)[1].split()[19]
Path(sys.argv[1]).write_text(json.dumps({'pid': worker.pid, 'token': token}))
'''
    def launch(args, **kwargs):
        child = original_popen([sys.executable, '-c', launcher_code, str(worker_info)], **kwargs)
        launchers.append(child)
        return child
    def running(pid):
        try:
            return (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except FileNotFoundError:
            return False
    monkeypatch.setattr('mint.comparison.tools.subprocess.Popen', launch)
    try:
        tools._execute(request, folder)
        worker = json.loads(worker_info.read_text())
        deadline = time.monotonic() + 5
        while running(worker['pid']) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not running(worker['pid']), 'Exited launcher must not leave an owned renderer running'
        assert len(launchers) == 1 and launchers[0].returncode == 0
        assert not (Path('/proc') / str(launchers[0].pid)).exists(), 'Launcher must be reaped after session cleanup'
        assert external.poll() is None, 'Independent sessions must not receive cleanup signals'
    finally:
        tools.close()
        # Clean only fixture identities if an assertion failed before controller cleanup.
        if worker_info.exists():
            worker = json.loads(worker_info.read_text())
            if _token(worker['pid']) == worker['token'] and running(worker['pid']):
                os.kill(worker['pid'], signal.SIGKILL)
        for child in launchers:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        if external.poll() is None:
            external.terminate()
        external.wait(timeout=5)


def test_indexed_artifact_reports_reference_withdrawal_without_reindexing(manager, tmp_path):
    comp, reference = _with_reference(manager, tmp_path)
    registry = ResearchRegistry(manager)
    folder = manager.root / 'runs' / reference / 'result'
    saved = registry.put('artifact', {'name': 'Reference indexed before correction',
        'type': 'published_geometry', 'run_id': reference, 'identity': file_identity(folder / 'result.json')})
    before = {path: file_identity(path) for path in folder.iterdir()}
    _withdraw(manager, reference)
    value = next(row for row in registry.page('artifact')['records'] if row['id'] == saved['id'])
    assert value['availability'] == 'annotation_withdrawn' and 'mean pose' in value['reason']
    assert {path: file_identity(path) for path in folder.iterdir()} == before
