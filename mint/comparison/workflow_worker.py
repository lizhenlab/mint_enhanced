"""Allowlisted workflow commands and physical GPU leases in an owned process."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
import threading
import traceback
from pathlib import Path

from mint.runtime import atomic_json, file_identity, digest
from mint.inference.cache_budget import file_lock, _token
from mint.inference.gpu_scheduler import GpuLease, NvidiaSmiProbe
from .registry import PROJECT


def acquire_devices(count, progress, cancelled):
    while not cancelled():
        leases, devices = [], []
        try:
            samples = NvidiaSmiProbe()()
            for uuid, sample in sorted(samples.items(), key=lambda v: -v[1].free_mib):
                if sample.free_mib < 28000 or sample.utilization > 10 or sample.foreign_pids:
                    continue
                lease = GpuLease(uuid)
                if lease.acquire():
                    leases.append(lease); devices.append(uuid)
                if len(devices) == count:
                    return devices, leases
        except Exception as error:
            progress(stage='waiting_gpu', detail=str(error))
        for lease in leases:
            lease.release()
        progress(stage='waiting_gpu', detail=f'训练/数据生产等待 {count} 张安全空闲 GPU；固定 world size，不抢占其他进程', done=0, total=count)
        time.sleep(2)
    raise InterruptedError('Workflow cancelled while waiting for GPU admission')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    path = Path(args.request).resolve()
    folder = path.parent
    request = json.loads(path.read_text())
    stopped = [False]
    parent_lost = [False]
    watch_stop = threading.Event()
    parent_pid = int(os.environ.get('MINT_WORKFLOW_PARENT_PID', '0'))
    parent_token = os.environ.get('MINT_WORKFLOW_PARENT_TOKEN')
    def watch_parent():
        while not watch_stop.wait(.5):
            if parent_pid and _token(parent_pid) != parent_token:
                parent_lost[0] = True
                stopped[0] = True
                return
    threading.Thread(target=watch_parent, name='workflow-parent-watch', daemon=True).start()
    def stop(*_):
        stopped[0] = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    cancelled = lambda: stopped[0] or (folder / 'cancel').exists()
    def progress(**values):
        atomic_json(folder / 'progress.json', {**values, 'updated_at': time.time(), 'pid': os.getpid()})
    child, leases = None, []
    status = 'failed'
    error = None
    try:
        from .jobs import ComparisonManager
        from .research import ResearchRegistry
        from .workflows import WorkflowManager
        manager = ComparisonManager(folder.parent.parent, autostart=False)
        registry = ResearchRegistry(manager)
        workflows = WorkflowManager(manager, registry, autostart=False)
        kind, p = request['kind'], request['parameters']
        check = workflows.preflight(kind, p)
        if not check['ready'] or check['snapshots'] != request['snapshots']:
            raise ValueError('Frozen workflow inputs changed')
        attempt = 1 + len(list(folder.glob('attempt-*.json')))
        attempt_file = folder / f'attempt-{attempt:04d}.json'
        atomic_json(attempt_file, {'started': time.time(), 'status': 'running', 'kind': kind})
        progress(stage='preparing', done=0, total=1, attempt=attempt)
        if cancelled():
            raise InterruptedError('Workflow cancelled before execution')
        if kind == 'native_import':
            from .native_sources import prepare_native, native_source_key
            plan = request['snapshots']['native_plan']
            output = prepare_native(plan, manager.root / 'native-sources' / digest(plan), cancelled=cancelled, progress=progress)
            training = output.get('training')
            dataset = registry.put('dataset', {'name': request['name'], 'status': 'native_prepared',
                'native': output, 'root': training['root'] if training else None,
                'training_ready': bool(training), 'split': plan['split'], 'frames': output['frames'],
                'supervision': output['supervision'], 'source_keys': [native_source_key(plan)],
                'workflow_id': request['id'], 'identity': file_identity(Path(output['reference']).parent / 'native.json')})
            output['dataset_id'] = dataset['id']
        elif kind == 'produce_pseudo':
            from .production import produce
            output = produce(request['snapshots']['production_plan'], folder / 'production',
                acceleration_limit=p['acceleration_limit'], cancelled=cancelled, progress=progress)
            data = output['dataset']
            record = registry.put('dataset', {'name': request['name'], **data, 'status': 'native_pseudo_labels',
                'label_role': 'model_generated_pseudo_labels', 'split': p['split'],
                'training_ready': p['split'] == 'train', 'source_keys': [v['source_key'] for v in output['plan']],
                'workflow_id': request['id'], 'requires_training_opt_in': 'data.allow_pseudo_labels=true'})
            output['dataset_id'] = record['id']
        elif kind in {'prepare_training', 'curate_dataset'}:
            from .training_data import prepare_existing_camera_dataset, export_reviewed_camera_dataset
            destination = folder / f'dataset-attempt-{attempt:04d}'
            if kind == 'prepare_training':
                output = prepare_existing_camera_dataset(p['source'], destination, episode_ids=p.get('episodes'), max_frames=p.get('max_frames'), cancelled=cancelled)
                parent = None
            else:
                curated = registry.get(p['dataset_id'], 'dataset')
                output = export_reviewed_camera_dataset(manager, curated, destination, cancelled=cancelled)
                parent = curated['id']
            dataset = registry.put('dataset', {'name': request['name'], 'root': output['root'],
                'frames': output['frames'], 'episodes': output['episodes'], 'identity': output['identity'],
                'provenance': output['provenance'], 'status': 'converted_camera_dataset', 'training_ready': True,
                'label_role': output['label_role'],
                'requires_training_opt_in': output['label_role'] == 'model_generated_pseudo_labels',
                'parent_curation_id': parent, 'entries': registry.get(parent, 'dataset')['entries'] if parent else [],
                'source_keys': [digest(v) for v in output['provenance']['label_origin'].get('identities', [])] if kind == 'prepare_training' else
                               [v.get('source_key') for v in curated['entries'] if v.get('source_key')],
                'workflow_id': request['id']})
            output['dataset_id'] = dataset['id']
        else:
            command = []
            env = os.environ.copy()
            env.update(PYTHONPATH=str(PROJECT), PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2',
                WANDB_MODE='disabled', WANDB_DISABLED='true', RUN_SERIES='workflow_' + request['id'][:12])
            if kind == 'diagnostic':
                command = [request['python'], '-m', 'mint', 'doctor', '--profile', p.get('profile', 'train')]
            if kind == 'train':
                from .training_curves import latest_checkpoint
                automatic_resume = latest_checkpoint(folder / 'training')
            if kind in {'train', 'pipeline'}:
                devices, leases = acquire_devices(p['gpus'], progress, cancelled)
                env['CUDA_VISIBLE_DEVICES'] = ','.join(devices)
                env['MINT_TRAIN_PROGRESS_PATH'] = str(folder / 'progress.json')
                env['MINT_TRAIN_ATTEMPT'] = str(attempt)
            if kind == 'train':
                import yaml
                config = yaml.safe_load(Path(p['config']).read_text())
                if 'allow_pseudo_labels' in p:
                    config.setdefault('data', {})['allow_pseudo_labels'] = p['allow_pseudo_labels']
                if p.get('dataset_id'):
                    config['data']['root'] = registry.get(p['dataset_id'], 'dataset')['root']
                config['data'].setdefault('camera_translation_normalization', {})['cache_dir'] = str(folder / 'normalization-cache')
                config['train']['wandb'] = {'enabled': False}
                if p.get('init_from'):
                    config['train']['init_from'] = p['init_from']
                configuration = folder / 'effective-config.yaml'
                configuration.write_text(yaml.safe_dump(config, sort_keys=False))
                resume = p.get('resume_checkpoint')
                for incomplete in (folder / 'training').glob('step_*'):
                    if incomplete.is_dir() and not (incomplete / 'checkpoint.json').is_file():
                        incomplete.rename(incomplete.with_name('.interrupted-' + incomplete.name + f'-attempt-{attempt}'))
                if automatic_resume:
                    resume = automatic_resume
                launch = [request['python']]
                if p['gpus'] > 1:
                    launch += ['-m', 'accelerate.commands.launch', '--multi_gpu', '--num_processes', str(p['gpus']),
                               '--num_machines', '1', '--mixed_precision', config['train'].get('mixed_precision', 'bf16'),
                               '--main_process_port', '0']
                command = [*launch, str(PROJECT / 'model_train/train.py'), '--config', str(configuration), '--output-dir', str(folder / 'training')]
                if resume:
                    command += ['--resume', resume]
                if p.get('max_steps'):
                    command += ['--max-steps', str(p['max_steps'])]
                progress(stage='loading_model', detail='恢复完整训练状态' if resume else '加载初始化权重和训练样本',
                         done=0, total=p.get('max_steps'), gpu_uuids=devices, attempt=attempt)
            if kind == 'pipeline':
                command = [request['python'], '-m', 'mint', 'pipeline', '--input', *p['inputs'],
                           '--output', str(folder / 'pipeline'), '--num-gpus', str(p['gpus']), '--frame_stride', str(p['frame_stride'])]
                progress(stage='data_pipeline', detail='Ray 数据生产任务；状态和 manifest 保存在本任务目录', gpu_uuids=devices)
            if not command:
                raise ValueError('Unknown workflow execution kind')
            atomic_json(folder / 'launch.json', {'command': command, 'gpu_uuids': env.get('CUDA_VISIBLE_DEVICES', ''),
                        'attempt': attempt, 'python': request['python'], 'configuration': check})
            child = subprocess.Popen(command, cwd=PROJECT, env=env, pass_fds=tuple(v.file.fileno() for v in leases))
            while child.poll() is None:
                if cancelled():
                    child.terminate()
                    raise InterruptedError('Workflow cancelled; complete checkpoints remain available')
                # Heartbeats retain the trainer's structured step/loss fields.
                try:
                    current = json.loads((folder / 'progress.json').read_text())
                    if time.time() - current['updated_at'] > 4:
                        progress(**{k: v for k, v in current.items() if k not in {'updated_at', 'pid'}})
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(.5)
            if child.returncode:
                raise RuntimeError(f'{kind} exited {child.returncode}; see the owned worker log')
            output = {'kind': kind, 'returncode': child.returncode}
            if kind == 'train':
                complete = latest_checkpoint(folder / 'training')
                if not complete:
                    raise ValueError('Training returned without an atomically complete checkpoint')
                weight = Path(complete) / 'model.safetensors'
                model = registry.register_model(path=str(weight), name=request['name'], config=str(folder / 'effective-config.yaml'),
                    dataset_id=p.get('dataset_id'), workflow_id=request['id'])
                output.update(model_id=model['id'], checkpoint=complete)
        if cancelled():
            raise InterruptedError('Workflow cancelled before publication')
        atomic_json(folder / 'output.json', output)
        completed = int(Path(output['checkpoint']).name.split('_')[-1]) if kind == 'train' else 1
        progress(stage='completed', done=completed, total=completed,
                 unit='optimizer_steps' if kind == 'train' else 'jobs', attempt=attempt)
        status = 'completed'
    except BaseException as exc:
        error = str(exc)
        status = 'interrupted' if parent_lost[0] else 'cancelled' if cancelled() or isinstance(exc, InterruptedError) else 'failed'
        if parent_lost[0]:
            error = 'Service owner exited; complete checkpoints are preserved for explicit resume'
        traceback.print_exc()
        progress(stage=status, detail=error)
    finally:
        watch_stop.set()
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(timeout=4)
        if status != 'completed' and os.getpgrp() == os.getpid():
            from mint.service import _owned_members
            members = [(pid, token) for pid, token in _owned_members(os.getpid(), _token(os.getpid())) if pid != os.getpid()]
            for pid, token in members:
                if _token(pid) == token:
                    try: os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError: pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and any(_token(pid) == token for pid, token in members):
                time.sleep(.1)
            for pid, token in members:
                if _token(pid) == token:
                    try: os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError: pass
        for lease in leases:
            lease.release()
        if 'attempt_file' in locals():
            atomic_json(attempt_file, {'started': json.loads(attempt_file.read_text())['started'], 'finished': time.time(), 'status': status, 'error': error})
        atomic_json(folder / 'exit.json', {'status': status, 'error': error, 'finished': time.time()})
    return int(status != 'completed')


if __name__ == '__main__':
    raise SystemExit(main())
