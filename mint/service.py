"""Supervise an owned Viewer child, record its identity and restart after crashes."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import errno
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from .runtime import atomic_json
from .inference.cache_budget import _token, file_lock


def _boot_id():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return None


def _active_registration(state_path):
    """An older supervisor may lack the lease, or have left a live child."""
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(previous, dict):
        return None
    boot_id = _boot_id()
    if previous.get('boot_id') and boot_id and previous['boot_id'] != boot_id:
        return None
    for field, token_field in (('pid', 'pid_token'), ('child_pid', 'child_token')):
        if field == 'pid' and previous.get('state') in {'stopped', 'failed'}:
            continue
        pid, token = previous.get(field), previous.get(token_field)
        if type(pid) is not int or pid <= 0 or not token or _token(pid) != token:
            continue
        proc = Path('/proc') / str(pid)
        if Path('/proc/self/stat').exists():
            try:
                if proc.stat().st_uid != os.getuid() or (proc / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                    continue
            except OSError:
                continue
        return pid
    return None


def _session_leader_matches(group, created):
    """The unreaped child anchors the session ID, including after its exit."""
    try:
        proc = Path('/proc') / str(group)
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        return (fields[19] == created and int(fields[2]) == group and int(fields[3]) == group
                and proc.stat().st_uid == os.getuid())
    except (OSError, ValueError, IndexError):
        return False


def _owned_members(group, created):
    """Never infer ownership from a recycled session number alone."""
    if not _session_leader_matches(group, created):
        return []
    members = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            if (fields[0] != 'Z' and int(fields[2]) == group and int(fields[3]) == group
                    and int(fields[19]) >= int(created) and proc.stat().st_uid == os.getuid()):
                members.append((int(proc.name), fields[19]))
        except (OSError, ValueError, IndexError):
            continue
    return members if _session_leader_matches(group, created) else []


def _signal_member(pid, token, group, created, signum):
    """Pin a member before validating it, so PID reuse cannot redirect a signal."""
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        return False
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    except OSError as error:
        if error.errno in {errno.ENOSYS, errno.EINVAL}:
            return False
        raise
    try:
        proc = Path('/proc') / str(pid)
        try:
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            matches = (fields[19] == token and int(fields[2]) == group and int(fields[3]) == group
                       and proc.stat().st_uid == os.getuid())
        except (OSError, ValueError, IndexError):
            matches = False
        if matches and _session_leader_matches(group, created):
            signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        pass
    finally:
        os.close(descriptor)
    return True


def _child_exited(child):
    if Path('/proc/self/stat').exists() and hasattr(os, 'waitid') and hasattr(os, 'WNOWAIT'):
        # Keep an exited leader as a zombie until its original session is clean.
        # Reaping it here would let another process reuse its PID/session number.
        try:
            return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return True
    return child.poll() is not None


def _reap_session(child, created, grace=30):
    """Retire survivors even if their original session leader has already exited."""
    if not Path('/proc/self/stat').exists():
        if child.poll() is None and _token(child.pid) == created:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=30)
        return
    deadline = time.monotonic() + grace
    while True:
        members = _owned_members(child.pid, created)
        if not members:
            return
        signum = signal.SIGTERM if time.monotonic() < deadline else signal.SIGKILL
        for pid, token in members:
            if not _signal_member(pid, token, child.pid, created, signum):
                # Older Linux kernels lack pidfds. The unreaped original leader
                # still pins this process group; another session cannot join it.
                if _session_leader_matches(child.pid, created):
                    try:
                        os.killpg(child.pid, signum)
                    except ProcessLookupError:
                        pass
                break
        if signum == signal.SIGKILL:
            return
        time.sleep(.05)


def supervise(command, state_path, log_path, *, restart_limit=5, restart_delay=2):
    """Hold one state owner through child exits, backoff and final cleanup."""
    state_path = Path(state_path).expanduser().resolve()
    with ExitStack() as leases:
        try:
            leases.enter_context(file_lock(state_path.with_name(state_path.name + '.lock'), blocking=False))
        except BlockingIOError as error:
            raise RuntimeError('A MINT supervisor already owns state: ' + str(state_path)) from error
        active = _active_registration(state_path)
        if active:
            raise RuntimeError(f'A registered MINT process is still active: {active} ({state_path})')
        return _supervise(command, state_path, log_path,
                          restart_limit=restart_limit, restart_delay=restart_delay)


def _supervise(command, state_path, log_path, *, restart_limit, restart_delay):
    stopping = threading.Event()
    previous_handlers = {}
    def stop(signum, frame): stopping.set()
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, stop)
    crashes, child, token = 0, None, None
    state = {'pid': os.getpid(), 'supervisor_pid': os.getpid(), 'pid_token': _token(os.getpid()),
             'command': command, 'log': str(log_path), 'started_at': time.time(), 'boot_id': _boot_id()}
    try:
        while not stopping.is_set():
            try:
                with Path(log_path).open('ab', buffering=0) as stream:
                    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                        stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            except OSError as error:
                # An invalid executable or unwritable log must not leave an
                # earlier process's stale "running" identity as the last state.
                state.update(state='failed', child_pid=None, child_token=None,
                             restart_count=crashes, error=f'{type(error).__name__}: {error}')
                atomic_json(state_path, state)
                return 1
            token = _token(child.pid) or _token(os.getpid())
            state.update(child_pid=child.pid, child_token=token, state='running', restart_count=crashes)
            atomic_json(state_path, state)
            while not _child_exited(child) and not stopping.wait(.25): pass
            _reap_session(child, token)
            child.wait(timeout=10)
            if stopping.is_set():
                break
            crashes += 1
            state.update(state='restarting', last_exit=child.returncode, restart_count=crashes)
            atomic_json(state_path, state)
            if crashes > restart_limit:
                state.update(state='failed')
                atomic_json(state_path, state)
                return 1
            stopping.wait(min(60, restart_delay * 2**(crashes-1)))
        state.update(state='stopped')
        atomic_json(state_path, state)
        return 0
    finally:
        try:
            if child is not None and token is not None:
                _reap_session(child, token)
        finally:
            for signum, handler in previous_handlers.items(): signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--log', required=True, type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command: parser.error('A child command is required after --')
    args.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        return supervise(command, args.state, args.log)
    except RuntimeError as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
