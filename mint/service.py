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


def supervise(command, state_path, log_path, *, restart_limit=5, restart_delay=2):
    stopping = threading.Event()
    previous_handlers = {}
    def stop(signum, frame): stopping.set()
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, stop)
    crashes, child = 0, None
    state = {'pid': os.getpid(), 'supervisor_pid': os.getpid(), 'pid_token': _token(os.getpid()),
             'command': command, 'log': str(log_path), 'started_at': time.time()}
    try:
        while not stopping.is_set():
            with Path(log_path).open('ab', buffering=0) as stream:
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                    stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            token = _token(child.pid)
            state.update(child_pid=child.pid, child_token=token, state='running', restart_count=crashes)
            atomic_json(state_path, state)
            while child.poll() is None and not stopping.wait(.25): pass
            if stopping.is_set():
                # Popen owns this child; verify PID creation identity before signalling its group.
                if child.poll() is None and _token(child.pid) == token:
                    os.killpg(child.pid, signal.SIGTERM)
                    try: child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        if _token(child.pid) == token:
                            os.killpg(child.pid, signal.SIGKILL)
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
