"""Supervisor restarts only its child and records a clean requested shutdown."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


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
