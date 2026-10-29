#!/usr/bin/env python3
"""Build a separate training/data/QA profile without replacing inference packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--with-backends', action='store_true', help='Also register public GeoCalib/MoGe sources and dependencies')
    parser.add_argument('--backend-archives', type=Path, help='Optional offline directory with utils3d-pinned.tar.gz and pipeline-pinned.tar.gz')
    parser.add_argument('--index-url', default='https://pypi.org/simple')
    args = parser.parse_args()
    root = args.root.resolve()
    core = root / '.venv/bin/python'
    target = root / '.venv-features'
    marker = target / '.mint-profile.json'
    if target.exists() and not marker.exists():
        raise RuntimeError('Existing feature environment is not managed by this installer: ' + str(target))
    if not target.exists():
        subprocess.run([str(core), '-m', 'venv', str(target)], check=True)
        marker.write_text(json.dumps({'base': str(core), 'profile': 'training-data-qa'})+'\n')
    python = target / 'bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '--index-url', args.index_url,
                    'pip==26.2.1', 'setuptools==80.9.0', 'wheel==0.45.1'], check=True)
    core_site = subprocess.check_output([str(core), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
    feature_site = Path(subprocess.check_output([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    # cmeel/Pinocchio and similar binary packages use their own .pth files.
    # addsitedir activates those paths as well as the shared numerical packages.
    (feature_site / 'mint-inference-foundation.pth').write_text('import site; site.addsitedir('+repr(core_site)+')\n')
    requirements = (args.source_root or root) / 'environments'
    plan_path = root / '.deployment/feature-install-plan.json'
    plan_path.parent.mkdir(exist_ok=True)
    command = [str(python), '-m', 'pip', 'install', '--index-url', args.index_url,
        '--constraint', str(requirements/'requirements-torch.txt'),
        '--constraint', str(requirements/'requirements-inference.txt'),
        '--constraint', str(requirements/'requirements-feature-constraints.txt'),
        '--requirement', str(requirements/'requirements-train.txt'),
        '--requirement', str(requirements/'requirements-data.txt'),
        '--requirement', str(requirements/'requirements-dev.txt')]
    if args.with_backends:
        backend_requirements = requirements / 'requirements-backends.txt'
        if args.backend_archives:
            lines = []
            for line in backend_requirements.read_text().splitlines():
                if ' @ https://' in line:
                    name, url = line.split(' @ ', 1)
                    archive = args.backend_archives.resolve() / (name + '-pinned.tar.gz')
                    if hashlib.sha256(archive.read_bytes()).hexdigest() != url.partition('#sha256=')[2]:
                        raise RuntimeError('Backend archive checksum mismatch: ' + name)
                    line = str(archive)
                lines.append(line)
            backend_requirements = plan_path.with_name('backend-install-requirements.txt')
            backend_requirements.write_text('\n'.join(lines)+'\n')
        command += ['--no-build-isolation', '--requirement', str(backend_requirements)]
    subprocess.run([*command, '--dry-run', '--report', str(plan_path)], check=True)
    plan = json.loads(plan_path.read_text())
    names = [item['metadata']['name'].lower().replace('_', '-') for item in plan.get('install', [])]
    if any(name in {'torch', 'torchvision', 'numpy', 'scipy', 'decord'} for name in names):
        raise RuntimeError('Feature plan would duplicate a core numerical package; inspect the constraints before installing')
    if args.install:
        subprocess.run(command, check=True)
        if args.with_backends:
            # Explicit dependencies above avoid slow VCS clones and keep the core constraints enforced.
            subprocess.run([str(python), '-m', 'pip', 'install', '--no-build-isolation', '--no-deps',
                '--editable', str(root/'third_party/GeoCalib'), '--editable', str(root/'third_party/MoGe')], check=True)
        subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    print(json.dumps({'profile': str(target), 'installed': args.install, 'planned_packages': names}))


if __name__ == '__main__':
    main()
