"""Persistent, queryable observations of heterogeneous owned task backends.

The source executor remains authoritative. Indexing never cancels a worker or
turns a missing heartbeat into failure. Phase durations are observed wall time.
"""
from __future__ import annotations

import json
import time

from mint.runtime import digest

TERMINAL = {'completed', 'partial', 'failed', 'cancelled', 'interrupted', 'evicted'}
WAIT_REASONS = {
    'queued': '等待执行槽位；交互任务优先，等待较久的批任务会逐步提升优先级',
    'waiting_gpu': '等待显存余量和物理 GPU 租约；不会终止其他用户的进程',
    'waiting_sample': '等待另一算法完成相同输入的解码缓存',
    'loading_model': '模型冷加载；当前尚未开始处理这一阶段的帧',
    'model_load': '模型冷加载；当前尚未开始处理这一阶段的帧',
    'hawor_slam': '连续 SLAM 与尺度估计具有时序依赖；其余可并行阶段单独调度',
    'slam_tracking': '连续 SLAM 逐帧跟踪；当前阶段具有时序依赖',
    'slam_global_ba': 'SLAM 全局束调整；正在优化关键帧和相机轨迹',
    'slam_trajectory': '正在为全部源帧补全相机轨迹',
    'cancelling': '等待当前安全边界退出并保存已完成的阶段',
    'preparing': '检查源文件、准备共享 RGB 和视频预览',
    'rendering': '从已有几何进行 CPU 编码，不重新运行推理网络',
}


class TaskIndex:
    def __init__(self, manager):
        self.manager = manager
        with manager.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS platform_tasks ('
                       'key TEXT PRIMARY KEY, id TEXT, kind TEXT, status TEXT, updated REAL, '
                       'search TEXT, payload TEXT, signature TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS platform_task_page ON platform_tasks(status,updated DESC)')
            db.execute('CREATE TABLE IF NOT EXISTS platform_events ('
                       'seq INTEGER PRIMARY KEY AUTOINCREMENT, task_key TEXT, at REAL, event TEXT, payload TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS platform_event_task ON platform_events(task_key,seq)')

    def sync(self, rows):
        now = time.time()
        with self.manager.db() as db:
            for incoming in rows:
                row = dict(incoming)
                key = row['kind'] + ':' + row['id']
                progress = row.get('progress') or {}
                phase = progress.get('stage') or progress.get('phase') or row['status']
                signature = digest(row)
                previous = db.execute('SELECT signature,payload FROM platform_tasks WHERE key=?', (key,)).fetchone()
                if previous and previous['signature'] == signature:
                    continue
                old = json.loads(previous['payload']) if previous else {}
                old_phase = old.get('phase')
                row['phase'] = phase
                row['phase_started_at'] = progress.get('phase_started_at') or (old.get('phase_started_at', now) if phase == old_phase else now)
                row['observed_at'] = now
                row['attempt'] = old.get('attempt', 0)
                starts = row['status'] not in TERMINAL | {'queued'} and (not old or old.get('status') in TERMINAL | {'queued'})
                if starts:
                    row['attempt'] += 1
                row['attempt'] = max(row['attempt'], len(row.get('attempts') or []))
                row['phase_history'] = old.get('phase_history', [])
                if old_phase and old_phase != phase:
                    row['phase_history'] = [*row['phase_history'], {'phase': old_phase,
                        'seconds': max(0, now - old.get('phase_started_at', now)), 'basis': 'observed_wall'}][-100:]
                detail = progress.get('detail') or progress.get('waiting_reason')
                row['waiting_reason'] = detail or WAIT_REASONS.get(phase) or WAIT_REASONS.get(row['status'])
                row['recovery_policy'] = row.get('recovery_policy') or (
                    'automatic_replay' if row['kind'] in {'batch', 'evaluation', 'export'} else
                    'explicit_resume' if row['kind'] in {'inference', 'render', 'workflow', 'study'} else 'backend_specific')
                if old.get('status') != row['status'] or old_phase != phase:
                    db.execute('INSERT INTO platform_events(task_key,at,event,payload) VALUES(?,?,?,?)',
                        (key, now, 'state', json.dumps({'status': row['status'], 'phase': phase, 'attempt': row['attempt'],
                         'owner_pid': row.get('owner_pid'), 'reason': row.get('waiting_reason')})))
                # Source timestamps are stable; a read must not make old jobs look new.
                row['updated'] = row.get('updated') or old.get('updated') or now
                search = json.dumps({k: row.get(k) for k in ('id', 'kind', 'label', 'source', 'status', 'phase', 'error')}, ensure_ascii=False).lower()
                db.execute('INSERT OR REPLACE INTO platform_tasks VALUES(?,?,?,?,?,?,?,?)',
                    (key, row['id'], row['kind'], row['status'], row['updated'], search,
                     json.dumps(row, ensure_ascii=False), signature))

    def recover_legacy(self):
        from mint.inference.cache_budget import _token
        with self.manager.db() as db:
            records = db.execute("SELECT payload FROM platform_tasks WHERE kind IN ('mint_batch','mint_benchmark','code_diff','dataset_analysis') AND status IN ('running','queued','cancelling')").fetchall()
        recovered = []
        for record in records:
            row = json.loads(record['payload'])
            if not row.get('owner_pid') or _token(row['owner_pid']) != row.get('owner_token'):
                recovered.append({**row, 'status': 'interrupted', 'updated': time.time(),
                    'error': 'Original in-memory executor exited; saved parameters remain available for explicit resubmission'})
        self.sync(recovered)

    def page(self, *, limit=100, offset=0, query='', status='all'):
        offset, limit = max(0, int(offset)), max(1, min(200, int(limit)))
        clauses, args = [], []
        if query:
            # instr avoids wildcard interpretation of user input.
            clauses.append('instr(search,?)>0'); args.append(str(query).strip().lower())
        if status == 'active':
            clauses.append("status NOT IN ('completed','partial','failed','cancelled','interrupted','evicted')")
        elif status == 'attention':
            clauses.append("status IN ('partial','failed','cancelled','interrupted','evicted')")
        elif status != 'all':
            clauses.append('status=?'); args.append(str(status))
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.manager.db() as db:
            total = db.execute('SELECT count(*) FROM platform_tasks' + where, args).fetchone()[0]
            records = db.execute('SELECT payload FROM platform_tasks' + where + ' ORDER BY updated DESC,key LIMIT ? OFFSET ?',
                                 [*args, limit, offset]).fetchall()
        rows = [json.loads(item['payload']) for item in records]
        now = time.time()
        for row in rows:
            elapsed = max(0, now - row.get('phase_started_at', now))
            row['phase_elapsed_seconds'] = elapsed
            p = row.get('progress') or {}
            done, count = p.get('done'), p.get('total')
            row['eta'] = None
            if row['status'] not in TERMINAL and isinstance(done, (int, float)) and isinstance(count, (int, float)) and 0 < done < count and elapsed > 2:
                row['eta'] = {'seconds': elapsed * (count - done) / done, 'scope': 'current_phase', 'basis': 'observed_linear',
                              'note': '阶段估算；模型冷加载、GPU 让步和下一阶段可能改变剩余时间'}
            heartbeat = p.get('updated_at') or row.get('updated')
            row['heartbeat_age_seconds'] = max(0, now - heartbeat) if heartbeat else None
        return {'tasks': rows, 'total': total, 'offset': offset}

    def detail(self, kind, task_id):
        key = str(kind) + ':' + str(task_id)
        with self.manager.db() as db:
            row = db.execute('SELECT payload FROM platform_tasks WHERE key=?', (key,)).fetchone()
            events = db.execute('SELECT seq,at,event,payload FROM platform_events WHERE task_key=? ORDER BY seq DESC LIMIT 200', (key,)).fetchall()
        if not row:
            raise KeyError('Indexed task not found')
        return {'task': json.loads(row['payload']), 'events': [{**dict(v), 'payload': json.loads(v['payload'])} for v in reversed(events)]}
