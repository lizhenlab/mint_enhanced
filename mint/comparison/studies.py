"""Persistent fixed-protocol regressions and honest resource/cache trials."""
from __future__ import annotations

import atexit
import json
import os
import threading
import time
import uuid

import numpy as np

from mint.runtime import digest, file_identity
from mint.inference.cache_budget import _token, file_lock
from .contracts import json_values, load_result, result_metadata
from .registry import backend_spec, normalize_options
from .research import validate_protocol_samples

TERMINAL = {'completed', 'partial', 'failed', 'cancelled', 'interrupted'}


class StudyManager:
    def __init__(self, manager, registry, workflows, *, autostart=True):
        self.manager, self.registry, self.workflows = manager, registry, workflows
        self.root = manager.root / 'studies'; self.root.mkdir(exist_ok=True)
        self.stop = threading.Event()
        interrupted_children = []
        with manager.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS studies (id TEXT PRIMARY KEY, payload TEXT, state TEXT, updated REAL)')
            rows = db.execute('SELECT id,state,payload FROM studies').fetchall()
            for row in rows:
                state = json.loads(row['state'])
                if state['status'] in {'launching', 'running', 'cancelling'} and _token(state.get('owner_pid', 0)) != state.get('owner_token'):
                    request = json.loads(row['payload'])
                    # Recover a child committed just before its coordinator lost
                    # the reply. Fresh keyed comparisons are idempotent.
                    for index, _ in enumerate(request['cases']):
                        for count in request['gpu_counts']:
                            for repeat in range(request['repeats']):
                                tag = f'{index}:{count}:{repeat}'
                                cid = uuid.uuid5(uuid.NAMESPACE_URL, 'study:' + row['id'] + ':' + tag).hex
                                prior = db.execute('SELECT payload,created FROM comparisons WHERE id=?', (cid,)).fetchone()
                                if prior:
                                    comp = json.loads(prior['payload'])
                                    child = state['children'].get(tag, {'comparison_id': cid,
                                        'owned': comp.get('created_run_ids', []), 'started': prior['created']})
                                    child['runs'] = self._prediction_runs(request, request['cases'][index], count,
                                        comp, study_id=row['id'], tag=tag)
                                    child['owned'] = [rid for rid in child.get('owned', []) if rid in child['runs']]
                                    state['children'][tag] = child
                    state.update(status='interrupted', error='Coordinator stopped; explicit resume reuses recorded child IDs')
                    db.execute('UPDATE studies SET state=?,updated=? WHERE id=?', (json.dumps(state), time.time(), row['id']))
                    interrupted_children.extend(state['children'].values())
        for child in interrupted_children:
            self._cancel_child(child)
        if autostart:
            threading.Thread(target=self._loop, name='platform-studies', daemon=True).start()
        atexit.register(self.close)

    def close(self):
        self.stop.set()

    def get(self, key):
        with self.manager.db() as db:
            row = db.execute('SELECT * FROM studies WHERE id=?', (str(key),)).fetchone()
        if not row:
            raise KeyError('Study not found')
        value = {'id': row['id'], 'request': json.loads(row['payload']), **json.loads(row['state']), 'updated': row['updated']}
        if value['status'] == 'completed':
            try:
                for case in value['request']['cases']:
                    self._verify_reference(case)
            except (OSError, ValueError, KeyError) as error:
                # Keep the original completion and timing rows for audit, but
                # never present a withdrawn reference as a valid cached study.
                value.update(status='failed', source_invalidated=True,
                             error='Study reference is unavailable or withdrawn: ' + str(error))
        return value

    def _set(self, key, *, _expected=None, **changes):
        with self.manager.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM studies WHERE id=?', (key,)).fetchone()
            state = json.loads(row['state'])
            if _expected is not None and state['status'] not in _expected:
                return False
            state.update(changes)
            db.execute('UPDATE studies SET state=?,updated=? WHERE id=?', (json.dumps(state), time.time(), key))
        return True

    def _prediction_runs(self, request, case, count, comparison, *, study_id, tag):
        """Restore frozen variants, not every result later attached to their comparison."""
        expected = []
        for selection, version in zip(request['selections'], request['versions']):
            options = dict(selection.get('options') or {})
            if count:
                options['max_gpus'] = count
            expected.append((selection['algorithm'], version, normalize_options(selection['algorithm'], options)))
        found = {}
        algorithms = {selection['algorithm'] for selection in request['selections']}
        nonce = digest({'fresh_comparison': 'study:' + study_id + ':' + tag}) if count else None
        for run_id in dict.fromkeys(comparison['run_ids']):
            payload = json.loads(self.manager.row(run_id)['payload'])
            if (payload['algorithm'] not in algorithms or payload.get('import') or payload['sample_id'] != case['sample_id'] or
                    payload.get('fresh_nonce') not in {None, nonce}):
                continue
            variant = (payload['algorithm'], payload.get('backend_version', payload.get('backend', {}).get('version')),
                       normalize_options(payload['algorithm'], payload.get('options')))
            if variant not in expected:
                continue
            run = self.manager.run(run_id, summary=True)
            if (run.get('result') or {}).get('reference_role') == 'user_declared_ground_truth':
                continue
            found.setdefault(digest(variant), run_id)
        if any(digest(variant) not in found for variant in expected):
            raise ValueError('Stored study child is missing a frozen prediction variant')
        return list(dict.fromkeys(found[digest(variant)] for variant in expected))

    def create(self, *, kind, selections, protocol_id=None, comparison_id=None, split='test', gpu_counts=None, repeats=1,
               name='Fixed study', atol_m=1e-4):
        if kind not in {'regression', 'performance'} or not isinstance(selections, list) or not 1 <= len(selections) <= 8:
            raise ValueError('Choose regression/performance and 1..8 native variants')
        if any(s.get('algorithm') == 'reference' for s in selections):
            raise ValueError('Select native predictions; references come from the frozen protocol')
        versions = []
        for selection in selections:
            if set(selection) - {'algorithm', 'options', 'checkpoint', 'config'}:
                raise ValueError('Unknown selection fields')
            spec = backend_spec(selection['algorithm'], **{k: selection[k] for k in ('checkpoint', 'config') if selection.get(k)})
            if not spec['ready']:
                raise ValueError('Backend is unavailable: ' + spec['label'])
            versions.append(spec['version'])
        if split not in {'validation', 'test'}:
            raise ValueError('Regression split must be validation or test')
        if not np.isfinite(float(atol_m)) or not 0 < float(atol_m) <= .01:
            raise ValueError('Set a joint numerical tolerance in (0, .01] metres')
        if kind == 'regression':
            protocol = self.registry.get(protocol_id, 'protocol')
            validate_protocol_samples(protocol)
            cases = [e for e in protocol['entries'] if e['split'] == split]
            if not cases:
                raise ValueError('The frozen split has no cases')
            counts, repeats = [None], 1
        else:
            if len(selections) != 1:
                raise ValueError('Use one algorithm variant per resource study')
            comp = self.manager.comparison(comparison_id, include_sample=False)
            cases = [{'comparison_id': comparison_id, 'sample_request': comp['sample_request'], 'sample_id': comp['sample_id']}]
            counts = gpu_counts or [1, 2, 4, 8]
            if not isinstance(counts, list) or not counts or len(set(counts)) != len(counts) or any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 8 for n in counts):
                raise ValueError('Choose distinct integer GPU caps 1..8')
            if isinstance(repeats, bool) or int(repeats) != repeats or not 1 <= repeats <= 3:
                raise ValueError('Use 1..3 repeats')
        request = {'kind': kind, 'name': str(name)[:200], 'selections': selections, 'versions': versions,
                   'protocol_id': protocol_id, 'comparison_id': comparison_id, 'split': split, 'cases': cases,
                   'gpu_counts': counts, 'repeats': int(repeats), 'atol_m': float(atol_m)}
        for case in cases:
            self._verify_reference(case)
        key = uuid.uuid4().hex
        with self.manager.db() as db:
            db.execute('INSERT INTO studies VALUES(?,?,?,?)', (key, json.dumps(request), json.dumps({
                'status': 'queued', 'children': {}, 'rows': [], 'progress': {'done': 0, 'total': len(cases) * len(counts) * int(repeats)}}), time.time()))
        return self.get(key)

    def cancel(self, key):
        if not self._set(key, status='cancelled', _expected={'queued'}):
            self._set(key, status='cancelling', _expected={'launching', 'running'})
        for child in self.get(key)['children'].values():
            self._cancel_child(child)
        return self.get(key)

    def _cancel_child(self, child):
        for run_id in child.get('owned', []):
            if self.manager.run(run_id, summary=True)['status'] not in TERMINAL:
                self.manager.cancel_exclusive(run_id, child['comparison_id'])

    def resume(self, key):
        state = self.get(key)
        if state['status'] in {'interrupted', 'failed', 'cancelled', 'partial'}:
            self._verify(state['request'])
            for child in state['children'].values():
                resumed = False
                for run_id in child.get('owned', []):
                    if self.manager.run(run_id, summary=True)['status'] in {'interrupted', 'failed', 'cancelled'}:
                        self.manager.resume(run_id)
                        resumed = True
                if resumed:
                    child['resumed_attempts'] = child.get('resumed_attempts', 0) + 1
                    child.pop('finished', None)
            self._set(key, status='queued', error=None, children=state['children'])
        return self.get(key)

    def _verify(self, request):
        if request['kind'] == 'regression':
            validate_protocol_samples({'entries': request['cases']})
        for selection, version in zip(request['selections'], request['versions']):
            if backend_spec(selection['algorithm'], **{k: selection[k] for k in ('checkpoint', 'config') if selection.get(k)})['version'] != version:
                raise ValueError('Frozen algorithm/model changed; submit a new study')
        for case in request['cases']:
            source = case['sample_request']['source']
            if file_identity(source['path']) != source:
                raise ValueError('Frozen study source changed')
            self._verify_reference(case)

    def _verify_reference(self, case):
        if not case.get('reference_run'):
            return
        reference = self.manager.run(case['reference_run'], summary=True)
        if not reference['result']:
            raise ValueError('Frozen reference is unavailable or withdrawn; create a corrected protocol')
        if reference['sample_id'] != case['sample_id']:
            raise ValueError('Frozen reference no longer belongs to the study input')
        folder = self.manager.root / 'runs' / case['reference_run'] / 'result'
        if file_identity(folder / 'result.json') != case['reference_identity']:
            raise ValueError('Frozen reference changed')
        result_metadata(folder)

    def _wait(self, key, child):
        while not self.stop.wait(.5) and not self.manager._stopping.is_set():
            state = self.get(key)
            if state['status'] == 'cancelling':
                self._cancel_child(child)
                raise InterruptedError('Study cancelled; only exclusive owned children were cancelled')
            runs = [self.manager.run(r, summary=True) for r in child['runs']]
            if all(r['status'] in TERMINAL for r in runs):
                return runs
        raise InterruptedError('Study interrupted by service shutdown')

    def _execute(self, key):
        with file_lock(self.root / (key + '.lock'), blocking=False):
            state = self.get(key); request = state['request']
            if not self._set(key, status='running', owner_pid=os.getpid(), owner_token=_token(os.getpid()),
                             error=None, _expected={'launching', 'queued'}):
                for child in self.get(key)['children'].values():
                    self._cancel_child(child)
                self._set(key, status='cancelled', _expected={'cancelling'})
                return
            self._verify(request)
            rows, all_runs, baseline = [], [], None
            for case_index, case in enumerate(request['cases']):
                sample = case['sample_request']
                for count in request['gpu_counts']:
                    for repeat in range(request['repeats']):
                        tag = f'{case_index}:{count}:{repeat}'
                        state = self.get(key)
                        if state['status'] == 'cancelling' or self.stop.is_set() or self.manager._stopping.is_set():
                            raise InterruptedError('Study stopped')
                        self._verify_reference(case)
                        child = state['children'].get(tag)
                        if child is None:
                            selections = [dict(s) for s in request['selections']]
                            if count:
                                selections[0]['options'] = {**selections[0].get('options', {}), 'max_gpus': count}
                            started = time.time()
                            comp = self.manager.create(sample['source']['path'], selections,
                                **{k: sample[k] for k in ('view', 'target_fps', 'max_frames', 'start_frame', 'camera')},
                                comparison_key='study:' + key + ':' + tag, title=request['name'] + ' · ' + tag,
                                execution={'priority': 'background', 'cache': 'fresh' if count else 'exact'})
                            child = {'comparison_id': comp['id'], 'runs': list(comp['run_ids']),
                                     'owned': comp['created_run_ids'], 'started': started}
                            children = state['children']; children[tag] = child; self._set(key, children=children)
                            if case.get('reference_run'):
                                try:
                                    self.manager.attach(comp['id'], case['reference_run'])
                                except BaseException:
                                    self._cancel_child(child)
                                    raise
                        else:
                            with self.manager.db() as db:
                                stored = db.execute('SELECT payload FROM comparisons WHERE id=?', (child['comparison_id'],)).fetchone()
                            child['runs'] = self._prediction_runs(request, case, count, json.loads(stored['payload']),
                                study_id=key, tag=tag)
                            child['owned'] = [rid for rid in child.get('owned', []) if rid in child['runs']]
                            children = state['children']; children[tag] = child; self._set(key, children=children)
                        runs = self._wait(key, child)
                        child.setdefault('finished', max(child['started'], *(r['updated'] for r in runs)))
                        children = self.get(key)['children']; children[tag] = child; self._set(key, children=children)
                        all_runs.extend(child['runs'])
                        row = {'case': case_index, 'gpu_cap': count, 'repeat': repeat, 'comparison_id': child['comparison_id'],
                               'run_ids': child['runs'], 'statuses': [r['status'] for r in runs],
                               'wall_seconds_including_queue': child['finished'] - child['started'],
                               'resumed_attempts': child.get('resumed_attempts', 0)}
                        if count and runs[0]['result']:
                            rid = runs[0]['id']; folder = self.manager.root / 'runs' / rid / 'result'
                            metadata, arrays = load_result(folder)
                            if baseline is None:
                                baseline = rid
                            _, original = load_result(self.manager.root / 'runs' / baseline / 'result')
                            same_mask = np.array_equal(original['geometry_valid'], arrays['geometry_valid'])
                            finite = original['geometry_valid'] & arrays['geometry_valid']
                            difference = float(np.max(np.abs(original['joints_camera_m'][finite] - arrays['joints_camera_m'][finite]))) if finite.any() else None
                            timeline = np.array_equal(original['source_frame_indices'], arrays['source_frame_indices'])
                            presence_equal = np.array_equal(original.get('presence'), arrays.get('presence'))
                            K_delta = float(np.max(np.abs(original['K'] - arrays['K'])))
                            pose_delta = None
                            pose_masks_equal = ('T_world_camera' in original) == ('T_world_camera' in arrays)
                            if 'T_world_camera' in original and 'T_world_camera' in arrays:
                                pose_masks_equal = np.array_equal(original['pose_valid'], arrays['pose_valid'])
                                poses = original['pose_valid'] & arrays['pose_valid']
                                pose_delta = float(np.max(np.abs(original['T_world_camera'][poses] - arrays['T_world_camera'][poses]))) if poses.any() else None
                            row.update(timing_scope=('resumed owned task; may reuse validated stages; exclude from fresh trials' if child.get('resumed_attempts') else 'new native worker with fresh inference cache; prepared input and OS cache may be warm'),
                                timings=metadata.get('timings'), native_timings=metadata.get('native_timings'),
                                resources=metadata.get('host_resource_usage'), gpu=metadata.get('gpu_scheduler'),
                                max_joint_coordinate_delta_m=difference, geometry_masks_equal=same_mask,
                                presence_equal=presence_equal, timeline_equal=timeline, max_K_delta_px=K_delta,
                                max_pose_matrix_delta=pose_delta, pose_masks_equal=pose_masks_equal,
                                numerical_gate=bool(timeline and presence_equal and pose_masks_equal and same_mask and
                                    K_delta <= .001 and (pose_delta is None or pose_delta <= request['atol_m']) and
                                    difference is not None and difference <= request['atol_m']))
                            started = time.perf_counter()
                            reused = self.manager.create(sample['source']['path'], selections,
                                **{k: sample[k] for k in ('view', 'target_fps', 'max_frames', 'start_frame', 'camera')},
                                comparison_key='study-reuse:' + key + ':' + tag,
                                execution={'cache': 'compatible', 'priority': 'background'})
                            row['result_reuse'] = {'lookup_seconds': time.perf_counter() - started,
                                                   'run_ids': reused['run_ids'], 'evidence': reused.get('reuse'),
                                                   'scope': 'validated complete-result lookup; no new inference'}
                        rows.append(row)
                        self._set(key, rows=json_values(rows), progress={'done': len(rows), 'total': state['progress']['total']})
            evaluation = None
            if request['kind'] == 'regression':
                evaluation = self.registry.evaluate(protocol_id=request['protocol_id'], run_ids=all_runs,
                    name=request['name'], split=request['split'])
            passed = all(all(s == 'completed' for s in r['statuses']) and r.get('numerical_gate', True) for r in rows)
            if not self._set(key, status='completed' if passed else 'partial', evaluation_id=evaluation['id'] if evaluation else None,
                             completed_at=time.time(), _expected={'running'}):
                raise InterruptedError('Study cancelled before completion publication')

    def _loop(self):
        while not self.stop.wait(.5) and not self.manager._stopping.is_set():
            with self.manager.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT id FROM studies WHERE json_extract(state,'$.status')='queued' ORDER BY updated LIMIT 1").fetchone()
                if row:
                    raw = db.execute('SELECT state FROM studies WHERE id=?', (row['id'],)).fetchone()
                    state = json.loads(raw['state'])
                    state.update(status='launching', owner_pid=os.getpid(), owner_token=_token(os.getpid()))
                    db.execute('UPDATE studies SET state=?,updated=? WHERE id=?', (json.dumps(state), time.time(), row['id']))
            if not row:
                continue
            try:
                self._execute(row['id'])
            except InterruptedError as error:
                for child in self.get(row['id'])['children'].values():
                    self._cancel_child(child)
                self._set(row['id'], status='interrupted' if self.stop.is_set() or self.manager._stopping.is_set() else 'cancelled',
                          error=str(error), _expected={'launching', 'running', 'cancelling'})
            except BaseException as error:
                for child in self.get(row['id'])['children'].values():
                    self._cancel_child(child)
                self._set(row['id'], status='failed', error=str(error), _expected={'launching', 'running', 'cancelling'})

    def task_rows(self):
        with self.manager.db() as db:
            keys = [r['id'] for r in db.execute('SELECT id FROM studies ORDER BY updated DESC')]
        return [{'id': key, 'kind': 'study', 'label': value['request']['name'], 'source': value['request']['kind'],
                 'status': value['status'], 'progress': value['progress'], 'updated': value['updated'],
                 'owner_pid': value.get('owner_pid'), 'error': value.get('error'), 'url': '/compare?study=' + key,
                 'recovery_policy': 'explicit_resume_recorded_children'} for key in keys for value in [self.get(key)]]
