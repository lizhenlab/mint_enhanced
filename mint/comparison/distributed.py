"""Owned SSH/shared-filesystem launcher; fixed world size and independent leases.

Run ``python -m mint.comparison.distributed --help``. No cloud provisioning and
no remote source editing. A dead coordinator stops only its own worker groups.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from mint.runtime import atomic_json, digest, file_identity
from mint.inference.cache_budget import _token, file_lock
from .registry import PROJECT


def load_profile(path):
    profile = json.loads(Path(path).read_text())
    nodes = profile.get('nodes', [])
    root = Path(profile['shared_root']).expanduser().resolve()
    if not root.is_absolute() or not nodes or len(nodes) > 32:
        raise ValueError('Choose an absolute shared MINT root and 1..32 nodes')
    if any(root == Path(p) or root.is_relative_to(Path(p)) for p in ('/dataset', '/data1/lizhen_algo/hawor', '/data1/lizhen_algo/ace_ego_hand')):
        raise ValueError('The shared code root must be owned by MINT')
    for rank, node in enumerate(nodes):
        host = node['host']; count = node['gpus']
        if not isinstance(host, str) or not host or host.startswith('-') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in host):
            raise ValueError('Use a hostname/IP without shell syntax')
        if node['rank'] != rank or isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 8:
            raise ValueError('Contiguous ranks and 1..8 GPUs are required')
    if len({v['host'] for v in nodes}) != len(nodes) or len({v['gpus'] for v in nodes}) != 1:
        raise ValueError('Use distinct hosts with equal GPU counts')
    if profile.get('master_address') not in [n['host'] for n in nodes] or not 1024 <= int(profile.get('master_port', 0)) <= 65535:
        raise ValueError('Declare a configured master and unprivileged rendezvous port')
    return {**profile, 'shared_root': str(root)}


def local_host(host):
    return host in {'localhost', '127.0.0.1', '::1', socket.gethostname(), socket.getfqdn()}


def node_command(profile, node, arguments):
    root = Path(profile['shared_root'])
    argv = [str(root / '.environments/training/bin/python'), '-m', 'mint.comparison.distributed', *arguments]
    if local_host(node['host']):
        return argv
    # SSH requires a command string; each argument is shell-quoted independently.
    command = 'cd ' + shlex.quote(str(root)) + ' && PYTHONDONTWRITEBYTECODE=1 ' + shlex.join(argv)
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'IPQoS=none', node['host'], command]


def probe(config, root):
    import importlib.metadata
    import yaml
    root = Path(root).resolve(); config = Path(config).resolve()
    if PROJECT.resolve() != root or not config.is_relative_to(root):
        raise ValueError('Run the same MINT-owned shared root/config on each node')
    cfg = yaml.safe_load(config.read_text())
    files = [config, *sorted((root / 'model_train').rglob('*.py')),
             root / 'mint/comparison/distributed.py', root / 'mint/comparison/distributed_smoke.py',
             root / 'mint/comparison/training_curves.py']
    weights = cfg.get('train', {}).get('init_from')
    if weights:
        weights = Path(weights); weights = weights if weights.is_absolute() else root / weights
        files.append(weights / 'model.safetensors' if weights.is_dir() else weights)
    roots = cfg.get('data', {}).get('root', [])
    for entry in roots if isinstance(roots, list) else [roots]:
        source = Path(entry['root'] if isinstance(entry, dict) else entry)
        source = source if source.is_absolute() else root / source
        files += [source / 'meta/info.json']
        if (source / 'source_manifest.json').exists():
            files.append(source / 'source_manifest.json')
            manifest = json.loads((source / 'source_manifest.json').read_text())
            records = [manifest.get('data'), manifest.get('episodes'), *manifest.get('videos', [])]
            if any(r and file_identity(r['path']) != r for r in records):
                raise ValueError('Shared dataset labels/videos are missing or changed on this node')
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise ValueError('Distributed input missing: ' + ', '.join(missing[:4]))
    from .native_sources import sha256
    signatures = {str(p.relative_to(root)) if p.is_relative_to(root) else str(p):
                  {'size': p.stat().st_size, 'sha256': sha256(p)} for p in files}
    versions = {name: importlib.metadata.version(name) for name in ('torch', 'accelerate', 'safetensors', 'pyarrow', 'decord')}
    machine = Path('/etc/machine-id').read_text().strip() if Path('/etc/machine-id').exists() else socket.gethostname()
    return {'machine': hashlib.sha256(machine.encode()).hexdigest(), 'host': socket.gethostname(),
            'shared_root': str(root), 'files': signatures, 'packages': versions, 'at': time.time(),
            'contract': digest({'files': signatures, 'packages': versions})}


def worker(request_path, rank):
    from .workflow_worker import acquire_devices
    request_path = Path(request_path).resolve(); folder = request_path.parent
    request = json.loads(request_path.read_text()); profile = request['profile']; node = profile['nodes'][rank]
    status_file = folder / f'node-{rank}.json'; leases = []; child = None; token = None
    stopping = [False]
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.__setitem__(0, True))
    def cancelled():
        try:
            expired = time.time() - (folder / 'heartbeat').stat().st_mtime > 15
        except OSError:
            expired = True
        return stopping[0] or expired or (folder / 'cancel').exists()
    def progress(**values):
        atomic_json(status_file, {**values, 'pid': os.getpid(), 'token': _token(os.getpid()), 'rank': rank, 'at': time.time()})
    try:
        with file_lock(folder / f'node-{rank}.lock', blocking=False):
            current = probe(request['config'], profile['shared_root'])
            if current['contract'] != request['probes'][rank]['contract'] or current['machine'] != request['probes'][rank]['machine']:
                raise ValueError('Frozen distributed code/environment/input changed')
            devices, leases = acquire_devices(node['gpus'], progress, cancelled)
            progress(status='ready', gpu_uuids=devices)
            while not (folder / 'start').exists():
                if cancelled():
                    raise InterruptedError('Coordinator lost or cancelled before rendezvous')
                time.sleep(.3)
            env = os.environ.copy()
            env.update(CUDA_VISIBLE_DEVICES=','.join(devices), PYTHONPATH=profile['shared_root'], PYTHONDONTWRITEBYTECODE='1',
                       OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', WANDB_MODE='disabled', WANDB_DISABLED='true',
                       MINT_TRAIN_PROGRESS_PATH=str(folder / 'progress.json'), MINT_TRAIN_ATTEMPT='1')
            command = [sys.executable, '-m', 'torch.distributed.run', '--nnodes', str(len(profile['nodes'])),
                '--nproc_per_node', str(node['gpus']), '--node_rank', str(rank),
                '--master_addr', profile['master_address'], '--master_port', str(profile['master_port'])]
            if request['smoke']:
                command += ['--module', 'mint.comparison.distributed_smoke', '--output', str(folder)]
            else:
                command += [str(PROJECT / 'model_train/train.py'), '--config', request['config'],
                            '--output-dir', str(folder / 'training'), '--max-steps', str(request['max_steps'])]
                if request.get('resume'):
                    command += ['--resume', request['resume']]
            with (folder / f'node-{rank}.log').open('ab', buffering=0) as log:
                child = subprocess.Popen(command, cwd=PROJECT, env=env, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True, pass_fds=tuple(v.file.fileno() for v in leases))
            token = _token(child.pid)
            progress(status='running', gpu_uuids=devices, child_pid=child.pid, child_token=token)
            while child.poll() is None:
                if cancelled():
                    raise InterruptedError('Coordinator lost or cancellation requested')
                time.sleep(.3)
            if child.returncode:
                raise RuntimeError(f'Distributed rank {rank} exited {child.returncode}')
            progress(status='completed', gpu_uuids=devices)
    except BaseException as error:
        progress(status='interrupted' if isinstance(error, InterruptedError) else 'failed', error=str(error))
        return 1
    finally:
        if child is not None and child.poll() is None and _token(child.pid) == token:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if _token(child.pid) == token:
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for lease in leases:
            lease.release()
    return 0


def coordinate(profile, config, *, execute=False, smoke=False, max_steps=10, resume=None):
    config = Path(config).resolve(); root = Path(profile['shared_root'])
    if root != PROJECT.resolve() or not config.is_relative_to(root):
        raise ValueError('Start from the configured MINT code root')
    if isinstance(max_steps, bool) or not 1 <= int(max_steps) <= 10000000:
        raise ValueError('Choose positive maximum optimizer steps')
    probes = []
    for node in profile['nodes']:
        result = subprocess.run(node_command(profile, node, ['--probe', str(config), '--root', str(root)]),
            capture_output=True, text=True, cwd=root, timeout=60)
        if result.returncode:
            raise ValueError('Node preflight failed: ' + node['host'] + ': ' + result.stderr[-1500:])
        probes.append(json.loads(result.stdout))
    if len({p['machine'] for p in probes}) != len(probes):
        raise ValueError('Two host names resolve to the same physical machine; do not double-lease its GPUs')
    if len({p['contract'] for p in probes}) != 1 or any(abs(time.time() - p['at']) > 90 for p in probes):
        raise ValueError('Node code/dependencies/inputs differ, or node clocks need synchronization')
    if not execute:
        return {'ready': True, 'physical_nodes': len(probes), 'probes': probes, 'effect': 'Read-only preflight; no GPU workers started'}
    key = uuid.uuid4().hex; folder = root / '.cache/comparison/distributed' / key; folder.mkdir(parents=True)
    request = {'profile': profile, 'config': str(config), 'probes': probes, 'smoke': bool(smoke),
               'max_steps': int(max_steps), 'resume': resume, 'coordinator_pid': os.getpid(), 'coordinator_token': _token(os.getpid())}
    if resume:
        marker = Path(resume) / 'checkpoint.json'
        saved = json.loads(marker.read_text())
        if any(file_identity(Path(resume) / name) != value for name, value in saved['files'].items()) or len(list(Path(resume).glob('random_states*.pkl'))) != sum(n['gpus'] for n in profile['nodes']):
            raise ValueError('Strict distributed resume requires intact complete state and the same world size')
    atomic_json(folder / 'request.json', request); (folder / 'heartbeat').touch()
    children = []
    old_handlers = {sig: signal.signal(sig, lambda *_: (folder / 'cancel').touch()) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for node in profile['nodes']:
            log = (folder / f'ssh-{node["rank"]}.log').open('ab', buffering=0)
            try:
                children.append(subprocess.Popen(node_command(profile, node, ['--worker', str(folder / 'request.json'), '--rank', str(node['rank'])]),
                    cwd=root, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL))
            finally:
                log.close()
        started = time.monotonic(); launched = False
        while any(c.poll() is None for c in children):
            (folder / 'heartbeat').touch()
            states = [json.loads(p.read_text()) for p in sorted(folder.glob('node-*.json'))]
            if (folder / 'cancel').exists() or any(c.poll() not in (None, 0) for c in children):
                (folder / 'cancel').touch()
            elif not launched and len(states) == len(children) and all(s.get('status') == 'ready' for s in states):
                (folder / 'start').touch(); launched = True
            if not launched and time.monotonic() - started > 300:
                (folder / 'cancel').touch()
            atomic_json(folder / 'state.json', {'id': key, 'status': 'cancelling' if (folder / 'cancel').exists() else 'running', 'nodes': states})
            time.sleep(.5)
        states = [json.loads(p.read_text()) for p in sorted(folder.glob('node-*.json'))]
        success = len(states) == len(children) and all(c.returncode == 0 for c in children) and all(s.get('status') == 'completed' for s in states)
        result = {'id': key, 'status': 'completed' if success else 'interrupted', 'physical_nodes': len(probes),
                  'world_size': sum(n['gpus'] for n in profile['nodes']), 'nodes': states, 'directory': str(folder), 'smoke': bool(smoke)}
        atomic_json(folder / 'state.json', result); return result
    finally:
        (folder / 'cancel').touch()
        for c in children:
            try:
                c.wait(timeout=25)
            except subprocess.TimeoutExpired:
                c.terminate(); c.wait(timeout=5)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile'); parser.add_argument('--config'); parser.add_argument('--run', action='store_true')
    parser.add_argument('--smoke', action='store_true'); parser.add_argument('--max-steps', type=int, default=10)
    parser.add_argument('--resume'); parser.add_argument('--probe'); parser.add_argument('--root')
    parser.add_argument('--worker'); parser.add_argument('--rank', type=int, default=0)
    parser.add_argument('--cancel', help='Existing MINT-owned distributed job directory')
    args = parser.parse_args()
    if args.cancel:
        folder = Path(args.cancel).resolve()
        if not folder.is_relative_to(PROJECT / '.cache/comparison/distributed') or not (folder / 'request.json').is_file():
            raise ValueError('Choose an existing owned distributed job')
        (folder / 'cancel').touch(); return 0
    if args.worker:
        return worker(args.worker, args.rank)
    result = probe(args.probe, args.root) if args.probe else coordinate(load_profile(args.profile), args.config,
        execute=args.run, smoke=args.smoke, max_steps=args.max_steps, resume=args.resume)
    print(json.dumps(result, ensure_ascii=False)); return int(result.get('status', 'completed') != 'completed')


if __name__ == '__main__':
    raise SystemExit(main())
