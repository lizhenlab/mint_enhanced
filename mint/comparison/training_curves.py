"""Durable local curves without an external telemetry account."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path


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
                    attempts.add(row['attempt'])
                    points[row['step']] = row
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
    checkpoints = []
    for marker in sorted((folder / 'training').glob('step_*/checkpoint.json')):
        record = json.loads(marker.read_text())
        from mint.runtime import file_identity
        intact = bool(record.get('files')) and 'model.safetensors' in record['files'] and all(file_identity(marker.parent / name) == identity for name, identity in record['files'].items())
        checkpoints.append({'path': str(marker.parent), 'step': record.get('step'), 'complete': intact,
                            'model': str(marker.parent / 'model.safetensors')})
    return {'workflow_id': key, 'status': state['status'], 'points': rows, 'available_points': total,
            'attempts': sorted(attempts), 'tail_limited': truncated, 'checkpoints': checkpoints,
            'policy': 'Latest attempt per optimizer step; durable rank-0 scalar metrics; no partial accumulation steps'}
