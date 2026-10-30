"""Read-only task index and explicit bridges to the existing MINT workspace."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from mint.runtime import atomic_npz, digest, file_identity
from mint.inference.cache_budget import file_lock
from .jobs import TERMINAL


class WorkspaceServices:
    def __init__(self, manager, collections, tools, legacy=None, analysis=None):
        self.manager, self.collections, self.tools = manager, collections, tools
        self.legacy, self.analysis = legacy, analysis

    def cached_predictions(self):
        if self.legacy is None:
            return []
        rows = []
        for key, prediction in getattr(self.legacy, '_pred', {}).copy().items():
            eid, camera_mode, hand_mode = key
            item = self.legacy._items.get(eid, {})
            raw = self.legacy._raw.get(eid)
            if not raw or not isinstance(prediction.get('hand'), np.ndarray):
                continue
            source = item.get('path') or raw.get('video')
            if not source or not Path(source).is_file():
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
        item = self.legacy._items[entry['eid']]
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

    def tasks(self, *, limit=100, offset=0, query='', status='all'):
        from mint.inference.checkpoints import list_tasks
        rows, seen = [], set()
        with self.manager.db() as db:
            comparisons = db.execute('SELECT id,payload,created FROM comparisons ORDER BY created DESC').fetchall()
        for parent in comparisons:
            comparison = json.loads(parent['payload'])
            for run_id in comparison['run_ids']:
                if run_id in seen:
                    continue
                seen.add(run_id)
                run = self.manager.run(run_id)
                rows.append({'id': run_id, 'kind': 'inference', 'label': run['label'],
                    'source': comparison['sample_request']['source']['path'], 'status': run['status'],
                    'updated': run.get('updated', parent['created']), 'comparison_id': parent['id'],
                    'progress': run['progress'], 'error': run.get('error'), 'has_result': bool(run['result']),
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
                'source': params.get('source'), 'status': task['status'], 'updated': task.get('updated_at', 0),
                'progress': {'done': task.get('completed_windows', 0), 'total': task.get('total_windows', 0)},
                'url': '/?task=' + task['id']})
        if self.legacy is not None:
            snapshots = [('mint_batch', 'MINT 目录批量', self.legacy.batch_inference_status(), '/?tool=batch'),
                         ('mint_benchmark', '公开基准 / MINT 检查点评测', self.legacy.benchmark_status(), '/?tool=benchmark'),
                         ('code_diff', '配置与代码对比', self.legacy.logdiff_status(), '/?tool=code')]
            if self.analysis is not None:
                snapshots.append(('dataset_analysis', '数据集分析', self.analysis.status(), '/?tool=analysis-video'))
            for kind, label, snapshot, url in snapshots:
                if snapshot.get('running') or snapshot.get('done') or snapshot.get('error') or snapshot.get('results'):
                    rows.append({'id': kind, 'kind': kind, 'label': label,
                        'status': 'running' if snapshot.get('running') else 'failed' if snapshot.get('error') else 'completed',
                        'updated': snapshot.get('updated_at', time.time()), 'progress': {k: snapshot.get(k) for k in ('phase', 'stage', 'done', 'total')},
                        'error': snapshot.get('error'), 'url': url})
        exports = self.manager.root / 'exports'
        for path in exports.glob('*.json'):
            try:
                job = json.loads(path.read_text())
                if not job.get('id') or 'status' not in job:
                    continue
                rows.append({'id': job['id'], 'kind': 'export', 'label': '同步视频导出', 'status': job['status'],
                    'updated': path.stat().st_mtime, 'progress': {k: job.get(k) for k in ('done', 'total')},
                    'error': job.get('error'), 'url': job.get('url')})
            except (OSError, ValueError):
                continue
        query = str(query).lower().strip()
        rows = [row for row in rows if (not query or query in json.dumps(row, ensure_ascii=False).lower()) and
                (status == 'all' or (status == 'active' and row['status'] not in TERMINAL | {'evicted'}) or
                 (status == 'attention' and row['status'] in {'failed', 'partial', 'cancelled', 'interrupted', 'evicted'}) or row['status'] == status)]
        rows.sort(key=lambda row: row.get('updated') or 0, reverse=True)
        offset, limit = max(0, int(offset)), max(1, min(200, int(limit)))
        scheduler = getattr(getattr(self.legacy, 'predictor', None), 'gpu_scheduler_status', {}) if self.legacy else {}
        return {'tasks': rows[offset:offset + limit], 'total': len(rows), 'offset': offset,
                'resource': scheduler, 'cached_predictions': self.cached_predictions()}
