"""Supervise an owned Viewer child, record its identity and restart after crashes."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from .runtime import atomic_json
from .inference.cache_budget import _token


def _owned_members(group, created):
    """A child-created session cannot contain an unrelated user's process."""
    members = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            if (fields[0] != 'Z' and int(fields[2]) == group and int(fields[3]) == group
                    and int(fields[19]) >= int(created) and proc.stat().st_uid == os.getuid()):
                members.append((int(proc.name), fields[19]))
        except (OSError, ValueError, IndexError):
            continue
    return members


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
            try:
                if _token(pid) == token:
                    os.kill(pid, signum)
            except ProcessLookupError:
                pass
        child.poll()
        if signum == signal.SIGKILL:
            return
        time.sleep(.05)


def supervise(command, state_path, log_path, *, restart_limit=5, restart_delay=2):
    stopping = threading.Event()
    previous_handlers = {}
    def stop(signum, frame): stopping.set()
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, stop)
    crashes, child, token = 0, None, None
    state = {'pid': os.getpid(), 'supervisor_pid': os.getpid(), 'pid_token': _token(os.getpid()),
             'command': command, 'log': str(log_path), 'started_at': time.time()}
    try:
        while not stopping.is_set():
            with Path(log_path).open('ab', buffering=0) as stream:
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                    stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            token = _token(child.pid) or _token(os.getpid())
            state.update(child_pid=child.pid, child_token=token, state='running', restart_count=crashes)
            atomic_json(state_path, state)
            while child.poll() is None and not stopping.wait(.25): pass
            _reap_session(child, token)
            if stopping.is_set():
                child.wait(timeout=10)
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
        if child is not None and token is not None:
            _reap_session(child, token)
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
    return supervise(command, args.state, args.log)


if __name__ == '__main__':
    raise SystemExit(main())
