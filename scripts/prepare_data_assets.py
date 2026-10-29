#!/usr/bin/env python3
"""Fetch the three public pipeline weights from pinned official sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def metadata(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'MINT-asset-preparer'}), timeout=20) as response:
        return json.load(response)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 2**20), b''):
            value.update(block)
    return value.hexdigest()


def download(url, destination, expected=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and expected and sha256(destination) == expected:
        return expected
    partial = destination.with_suffix(destination.suffix + '.part')
    urls = [url]
    if url.startswith('https://huggingface.co/'):
        urls.append('https://hf-mirror.com/' + url[len('https://huggingface.co/'):])
    error = None
    for endpoint in urls:
        for attempt in range(2):
            try:
                command = ['curl', '--fail', '--location', '--silent', '--show-error',
                    '--continue-at', '-', '--connect-timeout', '15', '--max-time', '120',
                    '--speed-time', '20', '--speed-limit', '1024', '--output', str(partial), endpoint]
                subprocess.run(command, check=True, timeout=135)
                value = sha256(partial)
                if expected and value != expected:
                    partial.unlink()
                    raise ValueError('Checkpoint checksum mismatch')
                if partial.stat().st_size < 2**20:
                    raise ValueError('Checkpoint response is unexpectedly small')
                os.replace(partial, destination)
                return value
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                error = exc
                time.sleep(1)
    raise RuntimeError(str(error))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mint.runtime import atomic_json
    manifest_path = root / '.deployment/data-assets.json'
    try: previous = json.loads(manifest_path.read_text())
    except (OSError, ValueError): previous = {}
    assets = [
        {'name': 'MoGe-2', 'path': 'model/moge2/model.pt',
         'url': 'https://huggingface.co/Ruicheng/moge-2-vitl/resolve/39c4d5e957afe587e04eec59dc2bcc3be5ecd968/model.pt',
         'sha256': '3eefd4abb2102f38f12b2d1992e5ff15e4923e5431c67dd494afe157e0111cd5',
         'source': 'https://huggingface.co/Ruicheng/moge-2-vitl/commit/39c4d5e957afe587e04eec59dc2bcc3be5ecd968'},
        {'name': 'GeoCalib', 'path': 'model/geocalib/pinhole.tar',
         'url': 'https://github.com/cvg/GeoCalib/releases/download/v1.0/geocalib-pinhole.tar',
         'sha256': '86d6aeacd8bbd974c59ce39f61854e00d36911c732ad89be471476fd708722ac',
         'source': 'https://github.com/cvg/GeoCalib/releases/tag/v1.0'},
    ]
    try:
        commit = '7def6d801a3cc0b4e618578fcaee1eb0524303af'
        assets.append({'name': 'MegaSaM', 'path': 'model/megasam/megasam_final.pth',
            'url': f'https://raw.githubusercontent.com/mega-sam/mega-sam/{commit}/checkpoints/megasam_final.pth',
            'revision': commit, 'sha256': 'f386b79d7cf41ac3c339ec4364a40378a1529a9cee74271bedf5eb10e43e748a', 'source': f'https://github.com/mega-sam/mega-sam/blob/{commit}/checkpoints/megasam_final.pth'})
    except (OSError, ValueError, KeyError) as exc:
        previous['MegaSaM'] = {'status': 'failed', 'error': str(exc)}
    for asset in assets:
        prior = previous.get(asset['name'], {})
        expected = asset.get('sha256') or (prior.get('sha256') if prior.get('url') == asset['url'] else None)
        try:
            value = download(asset['url'], root / asset['path'], expected)
            previous[asset['name']] = {**asset, 'status': 'verified', 'sha256': value,
                                       'bytes': (root / asset['path']).stat().st_size}
        except Exception as exc:
            previous[asset['name']] = {**asset, 'status': 'failed', 'error': str(exc)}
        atomic_json(manifest_path, previous)
        print(json.dumps(previous[asset['name']]), flush=True)
    return 0 if all(item.get('status') == 'verified' for item in previous.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
