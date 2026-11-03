"""Durable local curves without an external telemetry account."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path


def checkpoint_summary(directory):
    """Describe an atomic full-state checkpoint without trusting its marker."""
    from mint.runtime import file_identity
    directory = Path(directory)
    result = {'path': str(directory), 'step': None, 'complete': False,
              'model': str(directory / 'model.safetensors')}
    try:
        record = json.loads((directory / 'checkpoint.json').read_text())
        files = record.get('files') if isinstance(record, dict) else None
        if not isinstance(files, dict) or not files or record.get('schema') != 1:
            raise ValueError('Incomplete training checkpoint marker')
        step = record.get('step')
        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            raise ValueError('Checkpoint has no valid optimizer step')
        result['step'] = step
        if directory.name.startswith('step_') and directory.name[5:] != f'{step:08d}' and directory.name[5:] != str(step):
            raise ValueError('Checkpoint directory and optimizer step differ')
        if any(not isinstance(name, str) or Path(name).name != name for name in files):
            raise ValueError('Checkpoint contains an invalid file name')
        if ('model.safetensors' not in files or not any(name.startswith('optimizer') for name in files) or
            not any(name.startswith('scheduler') for name in files) or
            not any(name.startswith('random_states') and name.endswith('.pkl') for name in files)):
            raise ValueError('Checkpoint lacks model, optimizer, scheduler or RNG state')
        if any(not isinstance(identity, dict) or file_identity(directory / name) != identity for name, identity in files.items()):
            raise ValueError('Training checkpoint file changed or is missing')
        result['complete'] = True
    except (OSError, ValueError, TypeError, KeyError) as error:
        result['error'] = str(error)
    return result


def latest_checkpoint(directory):
    """Fail before GPU admission if the most recent published state is damaged."""
    markers = list(Path(directory).glob('step_*/checkpoint.json'))
    if not markers:
        return None
    marker = max(markers, key=lambda path: int(path.parent.name[5:]) if path.parent.name[5:].isdigit() else -1)
    summary = checkpoint_summary(marker.parent)
    if not summary['complete']:
        raise ValueError('Latest training checkpoint is unavailable: ' + summary['error'])
    return summary['path']


def append_metrics(path, step, metrics, *, epoch=0, attempt='standalone'):
    values = {str(k): float(v) for k, v in metrics.items() if math.isfinite(float(v))}
    row = {'step': int(step), 'epoch': int(epoch), 'attempt': str(attempt), 'at': time.time(), 'metrics': values}
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    from mint.inference.cache_budget import file_lock
    with file_lock(path.with_suffix('.lock')), path.open('a+b') as stream:
        size = stream.seek(0, 2)
        if size:
            stream.seek(size - 1)
            if stream.read(1) != b'\n':
                cursor, end = size, 0
                while cursor:
                    start = max(0, cursor - 4096); stream.seek(start)
                    found = stream.read(cursor - start).rfind(b'\n')
                    if found >= 0:
                        end = start + found + 1; break
                    cursor = start
                stream.truncate(end)
        stream.seek(0, 2)
        stream.write((json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n').encode())
        stream.flush(); os.fsync(stream.fileno())


def read_curves(workflows, key, *, maximum=800):
    state = workflows.get(key)
    if (state.get('request') or {}).get('kind') not in {None, 'train'}:
        raise ValueError('Training curves are available only for training workflows')
    folder = workflows.root / key
    path = folder / 'metrics.jsonl'
    points, attempts = {}, set()
    truncated = False
    if path.is_file():
        with path.open('rb') as stream:
            truncated = path.stat().st_size > 8 * 2**20
            stream.seek(max(0, path.stat().st_size - 8 * 2**20))
            if truncated:
                stream.readline()
            for line in stream:
                try:
                    row = json.loads(line)
                    if (not isinstance(row, dict) or isinstance(row.get('step'), bool) or
                        not isinstance(row.get('step'), int) or row['step'] < 0 or
                        not isinstance(row.get('metrics'), dict)):
                        continue
                    attempt = row.setdefault('attempt', 'standalone')
                    if not isinstance(attempt, str):
                        continue
                    row['metrics'] = {key: value for key, value in row['metrics'].items()
                                      if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)}
                    step = row['step']
                    if attempt not in attempts:
                        # A resumed attempt can start before the previous one
                        # stopped. Keep the shared prefix and drop its old tail
                        # from the plotted series while retaining the log.
                        points = {index: point for index, point in points.items() if index < step}
                        attempts.add(attempt)
                    points[step] = row
                except (ValueError, KeyError, TypeError):
                    continue  # A killed writer can leave its final line incomplete.
    rows = [points[s] for s in sorted(points)]
    total = len(rows); maximum = min(2000, max(20, int(maximum)))
    if total > maximum:
        # Keep extrema of all metrics in each time bucket, then uniformly bound.
        import numpy as np
        indices = {0, total - 1}
        for bucket in np.array_split(np.arange(total), max(1, maximum // 8)):
            metrics = {k for i in bucket for k in rows[int(i)]['metrics']}
            for metric in metrics:
                candidates = [int(i) for i in bucket if metric in rows[int(i)]['metrics']]
                indices.update((min(candidates, key=lambda i: rows[i]['metrics'][metric]),
                                max(candidates, key=lambda i: rows[i]['metrics'][metric])))
        chosen = sorted(indices)
        if len(chosen) > maximum:
            chosen = [chosen[int(i)] for i in np.linspace(0, len(chosen) - 1, maximum)]
        rows = [rows[i] for i in chosen]
    checkpoints = [checkpoint_summary(marker.parent) for marker in (folder / 'training').glob('step_*/checkpoint.json')]
    checkpoints.sort(key=lambda row: (row['step'] is None, row['step'] or 0, row['path']))
    progress = state.get('progress')
    return {'workflow_id': key, 'status': state['status'], 'points': rows, 'available_points': total,
            'progress': progress if isinstance(progress, dict) else {}, 'error': state.get('error'),
            'attempts': sorted(attempts), 'tail_limited': truncated, 'checkpoints': checkpoints,
            'policy': 'Latest attempt replaces the previous curve from its first optimizer step; durable rank-0 scalar metrics'}
