"""Cooperative, expiring per-GPU admission intents; no signals to foreign jobs."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from mint.runtime import atomic_json
from .cache_budget import _token, file_lock

LEVELS = {'background': 0, 'batch': 1, 'interactive': 2}


class AdmissionIntent:
    def __init__(self, root, gpu, priority):
        self.root, self.gpu = Path(root) / 'priority', gpu
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.priority = LEVELS[priority]
        self.id = uuid.uuid4().hex
        self.path = self.root / (self.id + '.json')
        self.started = time.time()
        self.pid, self.token = os.getpid(), _token(os.getpid())

    def _rows(self, now):
        rows = []
        for path in self.root.glob('*.json'):
            try:
                value = json.loads(path.read_text())
                if value['updated'] < now - 8 or _token(value['pid']) != value['token']:
                    path.unlink(missing_ok=True)
                elif value['gpu'] == self.gpu:
                    rows.append(value)
            except (OSError, ValueError, KeyError):
                continue
        return rows

    @staticmethod
    def rank(value, now):
        # Aging guarantees progress even with a steady stream of interactive work.
        score = value['priority'] + min(2, int(max(0, now - value['created']) / 300))
        return (-score, value['created'], value['id'])

    def admitted(self):
        now = time.time()
        with file_lock(self.root / '.admission.lock'):
            atomic_json(self.path, {'id': self.id, 'pid': self.pid, 'token': self.token, 'gpu': self.gpu,
                        'priority': self.priority, 'created': self.started, 'updated': now})
            return min(self._rows(now), key=lambda value: self.rank(value, now))['id'] == self.id

    def higher_waiter(self):
        now = time.time()
        with file_lock(self.root / '.admission.lock'):
            return any(row['id'] != self.id and row['priority'] > self.priority for row in self._rows(now))

    def clear(self):
        self.path.unlink(missing_ok=True)


def configured_intent(root, gpu):
    priority = os.environ.get('MINT_TASK_PRIORITY')
    return AdmissionIntent(root, gpu, priority) if priority in LEVELS else None
