#!/usr/bin/env python3
"""Create a MINT-owned environment, reusing an existing inference stack read-only.

Example: .venv/bin/python scripts/setup_platform_environment.py --profile train
Never installs into the active inference environment or an external backend.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('train', 'qa', 'data'), required=True)
    parser.add_argument('--base-python', default=str(ROOT / '.venv/bin/python'))
    args = parser.parse_args()
    base = Path(args.base_python).expanduser().absolute()
    if not base.is_file():
        parser.error('Select the existing MINT inference interpreter')
    name = {'train': 'training', 'qa': 'platform-qa', 'data': 'data'}[args.profile]
    destination = ROOT / '.environments' / name
    if destination.is_symlink() or destination.exists() and not destination.is_dir():
        parser.error('Environment destination must be a MINT-owned directory')
    if not (destination / 'bin/python').exists():
        subprocess.run([str(base), '-m', 'venv', str(destination)], check=True)
    information = json.loads(subprocess.check_output([str(base), '-c',
        'import sysconfig,json;print(json.dumps({"purelib":sysconfig.get_path("purelib")}))'], text=True))
    site = next((destination / 'lib').glob('python*/site-packages'))
    (site / 'mint-inference-readonly.pth').write_text(information['purelib'] + '\n')
    requirements = ROOT / 'environments' / ('requirements-train.txt' if args.profile == 'train' else
        'requirements-data.txt' if args.profile == 'data' else 'requirements-platform-qa.txt')
    subprocess.run([str(destination / 'bin/python'), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
    from mint.runtime import atomic_json, file_identity
    packages = subprocess.check_output([str(destination / 'bin/python'), '-m', 'pip', 'freeze'], text=True)
    atomic_json(destination / 'mint-environment.json', {'profile': args.profile, 'python': str(destination / 'bin/python'),
        'base_interpreter': file_identity(base), 'read_only_base_site': information['purelib'],
        'requirements': file_identity(requirements), 'packages': packages.splitlines()})
    print(f'Environment ready: {destination}')


if __name__ == '__main__':
    main()
