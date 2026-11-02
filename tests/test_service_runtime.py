"""Supervisor restarts only its child and records a clean requested shutdown."""
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def _service_command(state, log, child):
    return [sys.executable, '-m', 'mint.service', '--state', str(state), '--log', str(log), '--', *child]


def _wait_state(process, path, expected):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        assert process.poll() is None
        if path.exists():
            value = json.loads(path.read_text())
            if value.get('state') == expected and value.get('pid') == process.pid:
                return value
        time.sleep(.02)
    raise AssertionError('Supervisor did not enter ' + expected)


def _stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        assert process.wait(8) == 0


def test_supervisor_recovers_child_and_stops_on_signal(tmp_path):
    marker = tmp_path / 'first-run'
    child = tmp_path / 'child.py'
    child.write_text('''import pathlib,time,sys
marker=pathlib.Path(sys.argv[1])
if not marker.exists():
    marker.write_text('first crash')
    raise SystemExit(7)
time.sleep(30)
''')
    state, log = tmp_path / 'state.json', tmp_path / 'service.log'
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, '-m', 'mint.service', '--state', str(state), '--log', str(log),
        '--', sys.executable, str(child), str(marker)], cwd=root, start_new_session=True)
    try:
        deadline = time.monotonic() + 12
        while True:
            assert process.poll() is None
            if state.exists():
                status = json.loads(state.read_text())
                if status.get('restart_count') == 1 and status.get('state') == 'running': break
            if time.monotonic() > deadline: raise AssertionError('Child did not restart')
            time.sleep(.05)
        assert status['pid'] == process.pid and status['child_pid'] != process.pid
        process.send_signal(signal.SIGTERM)
        assert process.wait(8) == 0
        assert json.loads(state.read_text())['state'] == 'stopped'
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(8)


def test_supervisor_has_one_state_owner_and_releases_lease_after_stop(tmp_path):
    state, log = tmp_path / 'service.json', tmp_path / 'service.log'
    root = Path(__file__).resolve().parents[1]
    command = _service_command(state, log, [sys.executable, '-c', 'import time;time.sleep(30)'])
    first = subprocess.Popen(command, cwd=root, start_new_session=True)
    try:
        original = _wait_state(first, state, 'running')
        before = state.read_bytes()
        # Equivalent paths share the same state lease, not only equal strings.
        (tmp_path / 'nested').mkdir()
        duplicate = _service_command(tmp_path / 'nested/../service.json', log, command[command.index('--') + 1:])
        rejected = subprocess.run(duplicate, cwd=root, capture_output=True, text=True, timeout=5)
        assert rejected.returncode == 2 and 'already owns state' in rejected.stderr
        assert first.poll() is None and state.read_bytes() == before
        assert json.loads(state.read_text())['child_pid'] == original['child_pid']
    finally:
        _stop(first)
    restarted = subprocess.Popen(command, cwd=root, start_new_session=True)
    try:
        assert _wait_state(restarted, state, 'running')['pid'] == restarted.pid
    finally:
        _stop(restarted)


def test_state_lease_is_held_while_child_is_in_restart_backoff(tmp_path):
    state, log = tmp_path / 'service.json', tmp_path / 'service.log'
    root = Path(__file__).resolve().parents[1]
    command = _service_command(state, log, [sys.executable, '-c', 'raise SystemExit(7)'])
    first = subprocess.Popen(command, cwd=root, start_new_session=True)
    try:
        status = _wait_state(first, state, 'restarting')
        assert status['restart_count'] == 1
        rejected = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=5)
        assert rejected.returncode == 2 and 'already owns state' in rejected.stderr
        assert json.loads(state.read_text())['pid'] == first.pid
    finally:
        _stop(first)


@pytest.mark.parametrize('field,token_field', [('pid', 'pid_token'), ('child_pid', 'child_token')])
def test_legacy_registered_process_is_not_replaced_or_signalled(tmp_path, field, token_field):
    from mint.inference.cache_budget import _token
    state, log = tmp_path / 'service.json', tmp_path / 'service.log'
    root = Path(__file__).resolve().parents[1]
    prior = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
    try:
        original = {field: prior.pid, token_field: _token(prior.pid), 'state': 'running'}
        state.write_text(json.dumps(original))
        command = _service_command(state, log, [sys.executable, '-c', 'import time;time.sleep(30)'])
        rejected = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=5)
        assert rejected.returncode == 2 and 'still active' in rejected.stderr
        assert json.loads(state.read_text()) == original and prior.poll() is None
        # A stale PID token is not an ownership claim on a different process.
        original[token_field] = 'not-the-current-creation-token'
        state.write_text(json.dumps(original))
        repaired = subprocess.Popen(command, cwd=root, start_new_session=True)
        try:
            _wait_state(repaired, state, 'running')
            assert prior.poll() is None
        finally:
            _stop(repaired)
    finally:
        prior.terminate()
        prior.wait(5)


@pytest.mark.parametrize('failure', ['missing_executable', 'unwritable_log'])
def test_start_failure_replaces_stale_running_state_and_releases_lease(tmp_path, failure):
    state = tmp_path / 'service.json'
    state.write_text(json.dumps({'state': 'running', 'pid': 123, 'child_pid': 456, 'child_token': 'old'}))
    root = Path(__file__).resolve().parents[1]
    child = [str(tmp_path / 'missing-program')] if failure == 'missing_executable' else [sys.executable, '-c', 'pass']
    log = tmp_path if failure == 'unwritable_log' else tmp_path / 'service.log'
    result = subprocess.run(_service_command(state, log, child), cwd=root, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1
    status = json.loads(state.read_text())
    assert status['state'] == 'failed' and status['pid'] != 123
    assert status['child_pid'] is None and status['child_token'] is None and status['error']
    # A failed launch cannot strand the lease and block a repaired command.
    corrected = subprocess.Popen(_service_command(state, tmp_path / 'valid.log',
        [sys.executable, '-c', 'import time;time.sleep(30)']), cwd=root, start_new_session=True)
    try:
        assert _wait_state(corrected, state, 'running')['pid'] == corrected.pid
    finally:
        _stop(corrected)


def test_failed_launch_restores_handlers_and_can_be_retried_in_the_same_process(tmp_path):
    from mint.service import supervise
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    for _ in range(2):
        assert supervise([str(tmp_path / 'missing-program')], tmp_path / 'state.json', tmp_path / 'log',
                         restart_limit=0, restart_delay=0) == 1
        assert {number: signal.getsignal(number) for number in previous} == previous


def _fake_procfs(monkeypatch, tmp_path):
    import mint.service as service
    directory = tmp_path / 'proc'
    (directory / 'self').mkdir(parents=True)
    (directory / 'self/stat').touch()

    def proc_path(value):
        value = Path(value)
        return directory / value.relative_to('/proc') if value.is_relative_to('/proc') else value

    monkeypatch.setattr(service, 'Path', proc_path)
    return directory


def _proc_record(directory, pid, *, token, group, session, state='S'):
    proc = directory / str(pid)
    proc.mkdir(exist_ok=True)
    fields = ['0'] * 20
    fields[0], fields[2], fields[3], fields[19] = state, str(group), str(session), token
    (proc / 'stat').write_text(f'{pid} (worker) ' + ' '.join(fields))
    return proc


@pytest.mark.parametrize('leader_present', [True, False])
def test_cleanup_rejects_recycled_session_id_without_signalling_same_uid_processes(monkeypatch, tmp_path, leader_present):
    import mint.service as service
    directory = _fake_procfs(monkeypatch, tmp_path)
    # These processes have the right UID and newer creation times, but belong
    # to a different session that reused the old leader's numeric PID.
    if leader_present:
        _proc_record(directory, 900, token='200', group=900, session=900)
    _proc_record(directory, 901, token='201', group=900, session=900)
    sent = []
    monkeypatch.setattr(service.os, 'kill', lambda *args: sent.append(args))
    monkeypatch.setattr(service.os, 'killpg', lambda *args: sent.append(args))
    monkeypatch.setattr(service.signal, 'pidfd_send_signal', lambda *args: sent.append(args), raising=False)
    assert service._owned_members(900, '100') == []
    service._reap_session(SimpleNamespace(pid=900), '100', grace=0)
    assert sent == []


def test_cleanup_reaps_original_session_members_after_leader_exit_with_pidfd(monkeypatch, tmp_path):
    import mint.service as service
    directory = _fake_procfs(monkeypatch, tmp_path)
    # A zombie still pins the original PID/session until cleanup is complete.
    _proc_record(directory, 900, token='100', group=900, session=900, state='Z')
    member = _proc_record(directory, 901, token='101', group=900, session=900)
    external = _proc_record(directory, 902, token='102', group=902, session=902)
    sent, closed = [], []
    monkeypatch.setattr(service.os, 'pidfd_open', lambda pid: pid + 1000, raising=False)
    monkeypatch.setattr(service.os, 'close', closed.append)

    def send(descriptor, signum):
        sent.append((descriptor, signum))
        (member / 'stat').unlink()
        member.rmdir()

    monkeypatch.setattr(service.signal, 'pidfd_send_signal', send, raising=False)
    assert service._owned_members(900, '100') == [(901, '101')]
    service._reap_session(SimpleNamespace(pid=900), '100', grace=0)
    assert sent == [(1901, signal.SIGKILL)] and closed == [1901]
    assert (external / 'stat').exists()


@pytest.mark.parametrize('reused', ['member', 'leader'])
def test_pidfd_signal_rechecks_session_and_member_identity_after_open(monkeypatch, tmp_path, reused):
    import mint.service as service
    directory = _fake_procfs(monkeypatch, tmp_path)
    _proc_record(directory, 900, token='100', group=900, session=900, state='Z')
    _proc_record(directory, 901, token='101', group=900, session=900)
    sent, closed = [], []

    def open_reused(pid):
        # Between enumeration and pidfd_open, either the member or the whole
        # session was replaced by unrelated same-UID processes.
        if reused == 'member':
            _proc_record(directory, pid, token='200', group=pid, session=pid)
        else:
            _proc_record(directory, 900, token='200', group=900, session=900)
        return 1901

    monkeypatch.setattr(service.os, 'pidfd_open', open_reused, raising=False)
    monkeypatch.setattr(service.os, 'close', closed.append)
    monkeypatch.setattr(service.signal, 'pidfd_send_signal', lambda *args: sent.append(args), raising=False)
    assert service._signal_member(901, '101', 900, '100', signal.SIGTERM)
    assert sent == [] and closed == [1901]


@pytest.mark.parametrize('pidfd_support', ['missing', 'old_kernel'])
def test_cleanup_without_pidfd_signals_only_the_pinned_original_group(monkeypatch, tmp_path, pidfd_support):
    import errno
    import mint.service as service
    directory = _fake_procfs(monkeypatch, tmp_path)
    _proc_record(directory, 900, token='100', group=900, session=900, state='Z')
    member = _proc_record(directory, 901, token='101', group=900, session=900)
    external = _proc_record(directory, 902, token='102', group=902, session=902)
    sent = []
    if pidfd_support == 'missing':
        monkeypatch.delattr(service.os, 'pidfd_open', raising=False)
    else:
        def unsupported(pid):
            raise OSError(errno.ENOSYS, 'Kernel does not support pidfds')
        monkeypatch.setattr(service.os, 'pidfd_open', unsupported, raising=False)
        monkeypatch.setattr(service.signal, 'pidfd_send_signal', lambda *args: pytest.fail('No valid pidfd'), raising=False)

    def killgroup(group, signum):
        sent.append((group, signum))
        (member / 'stat').unlink()
        member.rmdir()

    monkeypatch.setattr(service.os, 'killpg', killgroup)
    monkeypatch.setattr(service.os, 'kill', lambda *args: pytest.fail('Unpinned PID signal'))
    service._reap_session(SimpleNamespace(pid=900), '100', grace=0)
    assert sent == [(900, signal.SIGKILL)] and (external / 'stat').exists()


def test_child_exit_observation_preserves_session_leader_until_cleanup(monkeypatch, tmp_path):
    import mint.service as service
    _fake_procfs(monkeypatch, tmp_path)
    calls = []
    for name, value in [('P_PID', 1), ('WEXITED', 4), ('WNOHANG', 1), ('WNOWAIT', 16)]:
        monkeypatch.setattr(service.os, name, value, raising=False)

    def waitid(*args):
        calls.append(args)
        return SimpleNamespace(si_pid=900)

    monkeypatch.setattr(service.os, 'waitid', waitid, raising=False)
    assert service._child_exited(SimpleNamespace(pid=900))
    assert calls == [(1, 900, 4 | 1 | 16)]


@pytest.mark.skipif(not Path('/proc/self/stat').exists(), reason='Linux process-session regression')
def test_exited_launcher_descendants_are_cleaned_without_touching_other_session(tmp_path):
    from mint.inference.cache_budget import _token
    root = Path(__file__).resolve().parents[1]
    marker, state, log = tmp_path / 'descendant.json', tmp_path / 'state.json', tmp_path / 'service.log'
    launcher = tmp_path / 'launcher.py'
    launcher.write_text('''import json,subprocess,sys
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
token=(Path('/proc')/str(child.pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
Path(sys.argv[1]).write_text(json.dumps({'pid':child.pid,'token':token}))
''')
    external = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
    script = ('from mint.service import supervise; import sys; '
              'raise SystemExit(supervise(sys.argv[3:],sys.argv[1],sys.argv[2],restart_limit=0,restart_delay=0))')
    supervisor = subprocess.Popen([sys.executable, '-c', script, str(state), str(log),
        sys.executable, str(launcher), str(marker)], cwd=root, start_new_session=True)
    try:
        assert supervisor.wait(timeout=10) == 1
        descendant = json.loads(marker.read_text())
        proc = Path('/proc') / str(descendant['pid']) / 'stat'
        assert (_token(descendant['pid']) != descendant['token']
                or proc.read_text().rsplit(')', 1)[1].split()[0] == 'Z')
        assert external.poll() is None
        assert json.loads(state.read_text())['state'] == 'failed'
    finally:
        if supervisor.poll() is None:
            supervisor.terminate()
            supervisor.wait(10)
        external.terminate()
        external.wait(5)


def test_user_unit_preserves_registered_argv_and_only_renders(tmp_path):
    from scripts.render_user_service import main, render_user_service
    project = tmp_path / 'project %value $root'; project.mkdir()
    metadata = project / '.deployment/viewer.json'; metadata.parent.mkdir()
    command = [str(project / '.venv/bin/python'), '-m', 'mint.service',
        '--state', str(project / '.deployment/mint-supervisor.json'),
        '--log', str(project / '.deployment/viewer.log'), '--', str(project / 'run_mint.sh'), 'viewer',
        '--host', '127.0.0.1', '--port', '8011', '--devices', 'shared', '--fps', '0']
    metadata.write_text(json.dumps({'command': command}))
    assert main(['--project', str(project)]) == 0
    rendered = (project / '.deployment/mint-viewer.service').read_text()
    assert rendered == render_user_service(project, {'command': command})
    assert 'ExecStart=:' in rendered and '%%value $root' in rendered and '"--devices" "shared"' in rendered
    working_directory = next(line for line in rendered.splitlines() if line.startswith('WorkingDirectory='))
    assert '$root' in working_directory and '$$root' not in working_directory
    assert working_directory == 'WorkingDirectory=' + str(project).replace('%', '%%')
    assert 'KillMode=mixed' in rendered and 'WantedBy=default.target' in rendered
    assert 'UnsetEnvironment=CUDA_VISIBLE_DEVICES MINT_MANO_DEVICE' in rendered
    assert not (project / '.config/systemd/user').exists()
    assert json.loads(metadata.read_text())['command'] == command
    with pytest.raises(ValueError, match='project-owned'):
        render_user_service(project, {'command': ['/bin/sh', '-c', 'other service']})
    with pytest.raises(ValueError, match='control characters'):
        render_user_service(project, {'command': command + ['bad\nargument']})
    analyzer = shutil.which('systemd-analyze')
    if analyzer:
        # Use the real parser when present. It catches both an incorrectly
        # quoted WorkingDirectory and '$$' in the executable's literal path.
        (project / '.venv/bin').mkdir(parents=True)
        (project / '.venv/bin/python').symlink_to(sys.executable)
        runtime = tmp_path / 'runtime'; runtime.mkdir(mode=0o700)
        checked = subprocess.run([analyzer, '--user', 'verify', str(project / '.deployment/mint-viewer.service')],
                                 env={**os.environ, 'XDG_RUNTIME_DIR': str(runtime)},
                                 capture_output=True, text=True, timeout=15)
        assert checked.returncode == 0, checked.stderr
