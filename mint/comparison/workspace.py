"""Read-only task index and explicit bridges to the existing MINT workspace."""
from __future__ import annotations

import json
import os
import time
import threading
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from mint.runtime import atomic_npz, digest, file_identity
from mint.inference.cache_budget import file_lock
from .jobs import TERMINAL


class WorkspaceServices:
    def __init__(self, manager, collections, tools, legacy=None, analysis=None):
        self.manager, self.collections, self.tools = manager, collections, tools
        self.legacy, self.analysis = legacy, analysis
        from .task_index import TaskIndex
        from .observability import ResourceMonitor
        self.index = TaskIndex(manager)
        self.resources = ResourceMonitor(manager.root)
        self._index_lock = threading.Lock()
        self._indexed_at = 0
        self._run_cache = {}
        self.workflows = None
        self.studies = None

    def start(self):
        if getattr(self, '_watcher', None):
            return
        self.index.recover_legacy()
        def watch():
            while not self.manager._stopping.is_set():
                try:
                    self.refresh_index()
                except Exception:
                    # An index failure cannot stop a source executor; API reads
                    # expose their errors independently and retry on the next tick.
                    import logging
                    logging.getLogger(__name__).exception('Workspace index refresh failed')
                self.manager._stopping.wait(5)
        self._watcher = threading.Thread(target=watch, name='workspace-index', daemon=True)
        self._watcher.start()

    def _legacy_item(self, eid):
        """Read the public Store item contract, with compatibility for old adapters.

        The real Viewer uses an append-only list indexed by nonnegative eid;
        earlier bridge fixtures/adapters used dictionaries. A released/stale
        cache entry must not turn the whole workspace listing into an error.
        """
        if isinstance(eid, bool) or not isinstance(eid, (int, np.integer)) or eid < 0:
            return None
        try:
            getter = getattr(self.legacy, 'item', None)
            value = getter(int(eid)) if callable(getter) else getattr(self.legacy, '_items', {})[eid]
        except (IndexError, KeyError, TypeError):
            return None
        return dict(value) if isinstance(value, Mapping) else None

    def cached_predictions(self):
        if self.legacy is None:
            return []
        rows = []
        for key, prediction in getattr(self.legacy, '_pred', {}).copy().items():
            eid, camera_mode, hand_mode = key
            item = self._legacy_item(eid)
            raw = self.legacy._raw.get(eid)
            if item is None or not raw or not isinstance(prediction.get('hand'), np.ndarray):
                continue
            episode = item.get('ep') or {}
            source = item.get('path') or raw.get('video') or (episode.get('video') if isinstance(episode, Mapping) else None)
            try:
                source = str(Path(source)) if source else None
                if not source or not Path(source).is_file():
                    continue
            except (OSError, TypeError, ValueError):
                continue
            hand_frame = 'camera' if item.get('kind') == 'lerobot' else item.get('hand_frame', getattr(self.legacy, 'default_hand_frame', 'camera'))
            identity = {'key': list(key), 'model': getattr(self.legacy.predictor, '_cache_model_identity', None), 'hand_frame': hand_frame,
                        'source': file_identity(source), 'frames': len(prediction['hand'])}
            rows.append({'id': digest(identity), 'eid': eid, 'camera_mode': camera_mode,
                'hand_mode': hand_mode, 'source': source, 'frames': len(prediction['hand']),
                'checkpoint': str(self.legacy.ckpt_path), 'identity': identity})
        return rows

    def import_prediction(self, legacy_id):
        entry = next((row for row in self.cached_predictions() if row['id'] == legacy_id), None)
        if entry is None:
            raise ValueError('原工作台预测不在内存中；请在原工作台加载已有结果，或导入已保存的 NPZ')
        key = (entry['eid'], entry['camera_mode'], entry['hand_mode'])
        prediction = self.legacy._pred.get(key)
        raw = self.legacy._raw.get(entry['eid'])
        if prediction is None or raw is None:
            raise ValueError('Cached prediction was released; retry after loading the saved result')
        self.legacy._validate_input_version(entry['eid'])
        item = self._legacy_item(entry['eid'])
        if item is None:
            raise ValueError('Cached input metadata was released; reload the saved result')
        if getattr(self.legacy.predictor, '_cache_model_identity', None) != entry['identity']['model']:
            raise ValueError('Workspace model changed; select its new cached result')
        import hashlib
        values = {name: np.asarray(value) for name, value in prediction.items()
                  if not name.startswith('_') and isinstance(value, np.ndarray) and value.dtype.kind in 'bifu'}
        numerical = hashlib.sha256()
        for name, value in sorted(values.items()):
            numerical.update((name + str(value.shape) + str(value.dtype)).encode())
            numerical.update(np.ascontiguousarray(value).tobytes())
        snapshot_id = digest({'entry': entry['id'], 'numerical': numerical.hexdigest()})
        folder = self.manager.root / 'legacy-imports' / snapshot_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'prediction.npz'
        with file_lock(folder / '.snapshot.lock'):
            if not path.exists():
                atomic_npz(path, **values)
        episode = item.get('ep', {})
        start = int(raw.get('source_indices', [episode.get('video_start', episode.get('start', 0))])[0])
        fps = float(raw.get('fps', item.get('fps', 0)) or 0)
        n = len(prediction['hand'])
        source = entry['source']
        if file_identity(source) != entry['identity']['source']:
            raise ValueError('Source changed during cached result export')
        if getattr(self.legacy.predictor, '_cache_model_identity', None) != entry['identity']['model']:
            raise ValueError('Workspace model changed during cached result export')
        return self.manager.create(source, [{'algorithm': 'mint'}], view=item.get('video_view', 'full'),
            start_frame=start, target_fps=fps, max_frames=n, title='工作台结果 · ' + Path(source).name,
            import_entry={'path': str(path), 'format': 'mint_npz', 'frame_map': list(range(n)), 'hand_frame': entry['identity']['hand_frame'],
                'declared_source': json.dumps({'source': 'Existing MINT workspace numerical prediction',
                    'checkpoint': entry['checkpoint'], 'model_identity': entry['identity']['model'],
                    'camera_mode': entry['camera_mode'], 'hand_mode': entry['hand_mode'],
                    'scope': 'Existing prediction; render-only shape/FoV averaging is not reapplied'}, ensure_ascii=False)},
            comparison_key='legacy:' + snapshot_id)

    def _snapshot_tasks(self):
        from mint.inference.checkpoints import list_tasks
        rows, seen = [], set()
        with self.manager.db() as db:
            comparisons = db.execute('SELECT id,payload,created FROM comparisons ORDER BY created DESC').fetchall()
            run_rows = {v['id']: dict(v) for v in db.execute('SELECT * FROM runs')}
        parents = {}
        for parent in comparisons:
            for key in json.loads(parent['payload'])['run_ids']:
                parents.setdefault(key, []).append(parent['id'])
        for parent in comparisons:
            comparison = json.loads(parent['payload'])
            for run_id in comparison['run_ids']:
                if run_id in seen:
                    continue
                seen.add(run_id)
                record = run_rows.get(run_id)
                if not record:
                    continue
                folder = self.manager.root / 'runs' / run_id
                signature = (record['updated'], file_identity(folder / 'progress.json'), file_identity(folder / 'result/result.json'))
                cached = self._run_cache.get(run_id)
                if cached and cached[0] == signature and time.monotonic() - cached[1] < 30:
                    run = cached[2]
                else:
                    run = self.manager._run_record(record, summary=True)
                    self._run_cache[run_id] = signature, time.monotonic(), run
                progress = dict(run['progress'])
                if run['status'] == 'completed' and run['result']:
                    progress.update(done=run['result']['frames'], total=run['result']['frames'], unit='frames')
                rows.append({'id': run_id, 'kind': 'inference', 'label': run['label'],
                    'source': comparison['sample_request']['source']['path'], 'status': run['status'],
                    'updated': run.get('updated', parent['created']), 'created': run.get('created'), 'comparison_id': parent['id'],
                    'owner_pid': run.get('owner_pid'), 'worker_pid': run.get('worker_pid'), 'priority': run.get('priority'),
                    'parent_ids': parents[run_id],
                    'execution_summary': {k: run['result'].get(k) for k in ('timings', 'orchestration', 'parallel_stages',
                        'peak_rss_gib', 'peak_gpu_allocated_gib', 'observed_phase_wall_seconds', 'host_resource_usage')} if run['result'] else None,
                    'progress': progress, 'error': run.get('error'), 'has_result': bool(run['result']),
                    'url': f'/compare?id={parent["id"]}&run={run_id}'})
        for collection in self.collections.history(limit=None):
            rows.append({'id': collection['id'], 'kind': collection['kind'], 'label': collection['title'],
                         'status': collection['status'], 'updated': collection['updated'],
                         'progress': {'done': collection['done'], 'total': collection['total']},
                         'error': collection.get('error'), 'url': '/compare?collection=' + collection['id']})
        for tool in self.tools.history(limit=None):
            rows.append({'id': tool['id'], 'kind': 'render', 'label': tool['kind'], 'status': tool['status'],
                         'updated': tool.get('updated', tool.get('created', 0)), 'progress': tool.get('progress', {}),
                         'error': tool.get('error'), 'url': '/compare?tool_job=' + tool['id']})
        for task in list_tasks(limit=None):
            params = task.get('parameters', {})
            if params.get('kind') == 'comparison':
                continue
            rows.append({'id': task['id'], 'kind': 'mint_workspace', 'label': 'MINT 工作台窗口任务',
                'source': params.get('source'), 'status': 'completed' if task['status'] == 'inference_complete' else task['status'], 'updated': task.get('updated_at', 0),
                'progress': {'stage': task['status'], 'done': task.get('completed_windows', 0), 'total': task.get('total_windows', 0)},
                'url': '/diagnostics?task=' + task['id']})
        if self.legacy is not None:
            snapshots = [('mint_batch', 'MINT 目录批量', self.legacy.batch_inference_status(), '/diagnostics?tool=batch'),
                         ('mint_benchmark', '公开基准 / MINT 检查点评测', self.legacy.benchmark_status(), '/diagnostics?tool=benchmark'),
                         ('code_diff', '配置与代码对比', self.legacy.logdiff_status(), '/diagnostics?tool=code')]
            if self.analysis is not None:
                snapshots.append(('dataset_analysis', '数据集分析', self.analysis.status(), '/diagnostics?tool=analysis-video'))
            for kind, label, snapshot, url in snapshots:
                if snapshot.get('running') or snapshot.get('done') or snapshot.get('error') or snapshot.get('results'):
                    identity = snapshot.get('started_at') or snapshot.get('out') or snapshot.get('output_dir') or 'latest'
                    from mint.inference.cache_budget import _token
                    rows.append({'id': digest({'kind': kind, 'started': identity}), 'kind': kind, 'label': label,
                        'status': 'running' if snapshot.get('running') else 'failed' if snapshot.get('error') else 'completed',
                        'owner_pid': os.getpid(), 'owner_token': _token(os.getpid()),
                        'parameters': {k: snapshot.get(k) for k in ('selection', 'models', 'input_dir', 'output_dir', 'started_at') if k in snapshot},
                        'recovery_policy': 'resubmit_saved_parameters_in_original_tool',
                        'updated': snapshot.get('updated_at'), 'progress': {k: snapshot.get(k) for k in ('phase', 'stage', 'done', 'total')},
                        'error': snapshot.get('error'), 'url': url})
        exports = self.manager.root / 'exports'
        for path in exports.glob('*.json'):
            try:
                job = json.loads(path.read_text())
                if not job.get('id') or 'status' not in job:
                    continue
                video = path.with_suffix('.mp4')
                export_status = 'evicted' if job['status'] == 'completed' and not video.is_file() else job['status']
                url = f'/api/comparison/exports/{job["id"]}/download' if export_status == 'completed' else None
                rows.append({'id': job['id'], 'kind': 'export', 'label': '同步视频导出', 'status': export_status,
                    'updated': job.get('updated_at', path.stat().st_mtime), 'attempts': job.get('attempts', []),
                    'owner_pid': job.get('owner_pid'), 'comparison_id': job.get('comparison_id'),
                    'recovery_policy': job.get('recovery_policy'), 'progress': {k: job.get(k) for k in ('done', 'total')},
                    'error': job.get('error'), 'url': url})
            except (OSError, ValueError):
                continue
        if self.workflows is not None:
            rows.extend(self.workflows.task_rows())
        if self.studies is not None:
            rows.extend(self.studies.task_rows())
        return rows

    def refresh_index(self):
        with self._index_lock:
            self.index.sync(self._snapshot_tasks())
            self._indexed_at = time.monotonic()

    def tasks(self, *, limit=100, offset=0, query='', status='all'):
        self.refresh_index()
        page = self.index.page(limit=limit, offset=offset, query=query, status=status)
        scheduler = getattr(getattr(self.legacy, 'predictor', None), 'gpu_scheduler_status', {}) if self.legacy else {}
        return {**page, 'resource': scheduler, 'resources': self.resources.snapshot(),
                'cached_predictions': self.cached_predictions()}
