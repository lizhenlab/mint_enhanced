"""Render a user systemd unit from the registered MINT service command.

This writes a reviewable file only. It does not install a unit, stop an existing
process, enable lingering, or alter another user's service.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _argument(value):
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError('Service arguments must be nonempty strings without control characters')
    # ExecStart uses ':' below to preserve every registered argument literally.
    escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    return '"' + escaped + '"'


def render_user_service(project, metadata):
    project = Path(project).expanduser().resolve()
    if str(project)[-1].isspace():
        raise ValueError('A systemd WorkingDirectory cannot end with whitespace')
    command = metadata.get('command')
    expected = [str(project / '.venv/bin/python'), '-m', 'mint.service',
                '--state', str(project / '.deployment/mint-supervisor.json'),
                '--log', str(project / '.deployment/viewer.log'), '--', str(project / 'run_mint.sh'), 'viewer']
    if not isinstance(command, list) or command[:len(expected)] != expected:
        raise ValueError('Choose the registered project-owned MINT Viewer supervisor command')
    arguments = ' '.join(_argument(value) for value in command)
    return '\n'.join([
        '[Unit]', 'Description=MINT Viewer and comparison workspace',
        'StartLimitIntervalSec=300', 'StartLimitBurst=5', '',
        '[Service]', 'Type=simple', 'WorkingDirectory=' + str(project).replace('%', '%%'),
        'ExecStart=:' + arguments, 'Environment=PYTHONUNBUFFERED=1',
        'UnsetEnvironment=CUDA_VISIBLE_DEVICES MINT_MANO_DEVICE',
        'Restart=on-failure', 'RestartSec=5', 'TimeoutStopSec=90',
        'KillMode=mixed', 'StandardOutput=journal', 'StandardError=inherit', '',
        '[Install]', 'WantedBy=default.target', '',
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    project = args.project.expanduser().resolve()
    metadata = json.loads((project / '.deployment/viewer.json').read_text())
    unit = render_user_service(project, metadata)
    output = project / '.deployment/mint-viewer.service'
    output.write_text(unit)
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
