"""Immutable experiment records, reviewed data and evaluation-gated model aliases."""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

from mint.runtime import atomic_json, digest, file_identity
from .contracts import result_metadata, load_result, json_values
from .evaluation import aggregate_pairs
from .registry import PROJECT
from .results import paired_metrics

KINDS = {'artifact', 'protocol', 'evaluation', 'review', 'dataset', 'model', 'experiment', 'deployment', 'annotation'}


def validate_protocol_samples(protocol):
    """A frozen input must not disappear into evaluation's sample-ID lookup."""
    seen = set()
    for entry in protocol['entries']:
        if entry['sample_id'] in seen:
            raise ValueError('固定协议包含重复输入；同一输入协议请选择一份比较，不能重复选择不同历史记录')
        seen.add(entry['sample_id'])


def training_source_keys(manager, training):
    """Include canonical native origins for older immutable registry versions."""
    from .native_sources import native_source_key, source_provenance
    keys = set(training.get('source_keys', [])) | {e['source_key'] for e in training.get('entries', []) if e.get('source_key')}
    if training.get('native'):
        keys.add(native_source_key(training['native']['plan']))
    for entry in training.get('entries', []):
        if entry.get('run_id'):
            request = json.loads(manager.row(entry['run_id'])['payload'])
            keys.add(source_provenance(manager, request['sample_request']['source'])['source_key'])
    for entry in training.get('provenance', {}).get('label_origin', {}).get('plan', []):
        if entry.get('sample', {}).get('source'):
            keys.add(source_provenance(manager, entry['sample']['source'])['source_key'])
    return keys


class ResearchRegistry:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.root / 'registry'
        self.root.mkdir(exist_ok=True)
        with manager.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS research_records (id TEXT PRIMARY KEY, kind TEXT, created REAL, search TEXT, payload TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS research_record_page ON research_records(kind,created DESC)')
            db.execute('CREATE TABLE IF NOT EXISTS model_aliases (name TEXT PRIMARY KEY, model_id TEXT, evaluation_id TEXT, updated REAL)')
            if 'configuration' not in {r[1] for r in db.execute('PRAGMA table_info(model_aliases)')}:
                db.execute("ALTER TABLE model_aliases ADD COLUMN configuration TEXT NOT NULL DEFAULT '{}'")
            db.execute('CREATE TABLE IF NOT EXISTS model_alias_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, model_id TEXT, previous TEXT, evaluation_id TEXT, at REAL)')
            if 'gate' not in {r[1] for r in db.execute('PRAGMA table_info(model_alias_events)')}:
                db.execute("ALTER TABLE model_alias_events ADD COLUMN gate TEXT NOT NULL DEFAULT '{}'")

    def put(self, kind, payload):
        if kind not in KINDS:
            raise ValueError('Unknown record kind')
        key = digest({'kind': kind, 'payload': payload})
        record = {'id': key, 'kind': kind, 'created': time.time(), **payload}
        with self.manager.db() as db:
            db.execute('INSERT OR IGNORE INTO research_records VALUES(?,?,?,?,?)',
                (key, kind, record['created'], json.dumps(record, ensure_ascii=False).lower(), json.dumps(record, ensure_ascii=False)))
        return self.get(key, kind)

    def get(self, key, kind=None):
        with self.manager.db() as db:
            row = db.execute('SELECT payload FROM research_records WHERE id=?', (str(key),)).fetchone()
        if not row:
            raise KeyError('Registry record not found')
        record = json.loads(row['payload'])
        if kind and record['kind'] != kind:
            raise ValueError('Registry record kind mismatch')
        return record

    def page(self, kind='artifact', *, query='', offset=0, limit=50):
        if kind not in KINDS:
            raise ValueError('Unknown record kind')
        offset, limit = max(0, int(offset)), max(1, min(200, int(limit)))
        with self.manager.db() as db:
            total = db.execute('SELECT count(*) FROM research_records WHERE kind=? AND instr(search,?)>0', (kind, str(query).lower())).fetchone()[0]
            rows = db.execute('SELECT payload FROM research_records WHERE kind=? AND instr(search,?)>0 ORDER BY created DESC,id LIMIT ? OFFSET ?',
                (kind, str(query).lower(), limit, offset)).fetchall()
        records = [json.loads(row['payload']) for row in rows]
        if kind in {'artifact', 'model'}:
            for record in records:
                identity = record.get('identity')
                if identity:
                    record['availability'] = 'available' if file_identity(identity['path']) == identity else 'missing_or_changed'
                    if record['availability'] == 'available' and record.get('type') == 'published_geometry':
                        try:
                            result_metadata(Path(identity['path']).parent)
                        except (OSError, ValueError, KeyError):
                            record['availability'] = 'missing_or_changed'
        return {'records': records, 'total': total, 'offset': offset}

    def index_artifacts(self):
        with self.manager.db() as db:
            runs = db.execute("SELECT id,payload FROM runs WHERE status IN ('completed','partial')").fetchall()
        for row in runs:
            request = json.loads(row['payload'])
            try:
                folder = self.manager.root / 'runs' / row['id'] / 'result'
                metadata = result_metadata(folder)
                self.put('artifact', {'name': metadata.get('label', request['algorithm']), 'type': 'published_geometry',
                    'run_id': row['id'], 'sample_id': metadata['sample_id'], 'source': request.get('sample_request', {}).get('source'),
                    'frames': metadata['frames'], 'identity': file_identity(folder / 'result.json'),
                    'reference_role': metadata.get('reference_role'), 'origin': 'imported' if request.get('import') else 'native',
                    'importable': False, 'note': 'Already in the standard result catalog'})
            except (OSError, ValueError, KeyError):
                continue
        from mint.inference.checkpoints import task_root
        for path in task_root().glob('*/task.json'):
            try:
                task = json.loads(path.read_text())
                if task.get('status') not in {'completed', 'inference_complete'}:
                    continue
                self.put('artifact', {'name': 'Saved MINT windows · ' + task.get('id', path.parent.name)[:12],
                    'type': 'raw_windows', 'path': str(path), 'task_id': task.get('id', path.parent.name),
                    'identity': file_identity(path), 'frames': task.get('frames'), 'importable': False,
                    'note': 'Window cache; resume with the identical model/source/stitching contract in the MINT workspace'})
            except (OSError, ValueError):
                continue
        from mint.inference.checkpoints import list_tasks
        for task in list_tasks(limit=None):
            directory = task.get('outputs', {}).get('directory')
            if not directory or task.get('status') != 'completed':
                continue
            try:
                path = Path(directory) / 'prediction.npz'
                summary = json.loads((Path(directory) / 'summary.json').read_text())
                params = task['parameters']
                if not path.is_file() or not params.get('source'):
                    continue
                from .contracts import source_request
                sample = source_request(params['source'], view=params.get('video_view', 'full'),
                    target_fps=params.get('target_fps', 0), max_frames=summary['frames'],
                    start_frame=summary.get('source_first_frame', 0))
                source_identity = task.get('identity', {}).get('input', {}).get('source')
                if not source_identity or sample['source'] != source_identity:
                    continue
                self.put('artifact', {'name': 'Saved MINT prediction · ' + summary['input_name'],
                    'type': 'offline_prediction', 'algorithm': 'mint', 'identity': file_identity(path),
                    'sample': sample, 'frames': summary['frames'], 'importable': True,
                    'import_entry': {'path': str(path), 'format': 'mint_npz', 'hand_frame': 'camera',
                        'frame_map': list(range(summary['frames'])), 'declared_source': json.dumps({
                            'origin': 'Saved native MINT CLI prediction; camera-frame MANO contract',
                            'model_identity': summary.get('model_identity'), 'checkpoint': params.get('checkpoint'),
                            'cam_mode': summary.get('camera_mode'), 'hand_mode': summary.get('hand_mode')})}})
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def register_raw(self, *, source, algorithm, import_entry, view='full', target_fps=0, max_frames=None, start_frame=0, camera=None, name=None):
        from .contracts import source_request
        from .registry import ALGORITHMS
        if algorithm not in ALGORITHMS or not isinstance(import_entry, dict):
            raise ValueError('Declare the backend, format, frame map and provenance')
        path = Path(import_entry.get('path', '')).expanduser().resolve()
        if not path.is_file():
            raise ValueError('Saved numerical prediction is unavailable')
        sample = source_request(source, view=view, target_fps=target_fps, max_frames=max_frames, start_frame=start_frame, camera=camera)
        # Full validation is delegated to the same import boundary used by the UI.
        return self.put('artifact', {'name': str(name or path.name)[:200], 'type': 'offline_prediction',
            'algorithm': algorithm, 'identity': file_identity(path), 'sample': sample,
            'import_entry': dict(import_entry), 'importable': True,
            'note': 'Registered file and mapping; geometry validation occurs during the owned CPU import task'})

    def import_artifact(self, key):
        record = self.get(key, 'artifact')
        if not record.get('importable') or file_identity(record['identity']['path']) != record['identity']:
            raise ValueError('Offline artifact is unavailable or changed')
        sample = record['sample']
        if file_identity(sample['source']['path']) != sample['source']:
            raise ValueError('Offline artifact source changed')
        return self.manager.create(sample['source']['path'], [{'algorithm': record['algorithm']}],
            **{k: sample[k] for k in ('view', 'target_fps', 'max_frames', 'start_frame', 'camera')},
            import_entry=record['import_entry'], comparison_key='artifact:' + key, title=record['name'])

    def protocol(self, *, name, comparisons, splits=None, tags=None):
        if not isinstance(comparisons, list) or not comparisons or len(comparisons) > 1000 or len(set(comparisons)) != len(comparisons):
            raise ValueError('Freeze 1..1000 distinct comparisons')
        splits = dict(splits or {})
        if set(splits) - set(comparisons) or any(value not in {'train', 'validation', 'test'} for value in splits.values()):
            raise ValueError('Protocol splits must map comparison IDs to train/validation/test')
        entries, seen = [], {}
        for key in comparisons:
            comparison = self.manager.comparison(key, include_sample=False)
            split = splits.get(key, 'test')
            with self.manager.db() as db:
                native_row = db.execute("SELECT payload FROM research_records WHERE kind='dataset' AND json_extract(payload,'$.native.source')=? LIMIT 1",
                    (comparison['sample_request']['source']['path'],)).fetchone()
            native = json.loads(native_row['payload'])['native'] if native_row else None
            if native:
                official = native['plan']['split']
                if split != {'val': 'validation'}.get(official, official):
                    raise ValueError('Frozen protocol split conflicts with the native official partition')
                from .native_sources import native_source_key
                source_key = native_source_key(native['plan'])
            else:
                source_key = digest(comparison.get('dataset_context', {}).get('identity') or comparison['sample_request']['source'])
            if source_key in seen and seen[source_key] != split:
                raise ValueError('The same source/episode cannot appear in multiple protocol splits')
            seen[source_key] = split
            refs = [r for r in comparison['runs'] if r['result'] and r['result'].get('reference_role') == 'user_declared_ground_truth']
            if len(refs) != 1:
                raise ValueError('Each frozen input requires exactly one explicit GT result')
            reference = refs[0]
            _, arrays = load_result(self.manager.root / 'runs' / reference['id'] / 'result')
            import numpy as np
            if not arrays['geometry_valid'].any():
                raise ValueError('Reference contains no valid hand geometry')
            entries.append({'comparison_id': key, 'sample_id': comparison['sample_id'], 'source_key': source_key,
                'sample_request': comparison['sample_request'], 'dataset_context': comparison.get('dataset_context'), 'split': split,
                'reference_run': reference['id'], 'reference_identity': file_identity(self.manager.root / 'runs' / reference['id'] / 'result/result.json'),
                'gt_hand_slots': int(np.count_nonzero(arrays['geometry_valid']))})
        validate_protocol_samples({'entries': entries})
        return self.put('protocol', {'name': str(name)[:200], 'entries': entries, 'tags': list(tags or []),
            'metric_contract': 'same-input-openpose21-v1; geometry intersection, coverage includes missing/failed predictions',
            'input_information': 'GT is reference-only unless explicit calibration was frozen in the sample request'})

    def evaluate(self, *, protocol_id, run_ids, name='Offline fixed-protocol evaluation', split='test'):
        protocol = self.get(protocol_id, 'protocol')
        validate_protocol_samples(protocol)
        if split not in {'validation', 'test'} or not isinstance(run_ids, list) or not run_ids or len(run_ids) > 1000:
            raise ValueError('Choose validation/test and explicit published or failed run IDs')
        entries = {e['sample_id']: e for e in protocol['entries'] if e['split'] == split}
        if not entries:
            raise ValueError('Selected protocol split is empty')
        runs = [self.manager.run(key, summary=True) for key in dict.fromkeys(run_ids)]
        groups, rows = {}, []
        for run in runs:
            if run['sample_id'] not in entries or run['id'] == entries[run['sample_id']]['reference_run']:
                raise ValueError('Every prediction must belong to the selected frozen split and differ from its GT')
            request = json.loads(self.manager.row(run['id'])['payload'])
            model_path = request.get('backend', {}).get('checkpoint')
            if run['algorithm'] == 'mint' and not model_path:
                model_path = str(Path(request.get('backend', {}).get('root') or PROJECT) / 'checkpoints/model.safetensors')
                if not request.get('backend', {}).get('identity'):
                    model_path = str(PROJECT / 'checkpoints/model.safetensors')
            config_path = request.get('backend', {}).get('config') or str(
                Path(request.get('backend', {}).get('root') or PROJECT) / 'configs/training/mint_step2.yaml')
            if not request.get('backend', {}).get('identity'):
                config_path = str(PROJECT / 'configs/training/mint_step2.yaml')
            from .registry import inference_configuration
            frozen = request.get('backend', {}).get('identity', {})
            assets = frozen.get('assets', {})
            model_record = (assets.get(str(model_path)) or assets.get('checkpoints/model.safetensors') or
                            next((v for v in assets.values() if v.get('path') == str(model_path)), None)) if model_path else None
            configuration = frozen.get('selected_configuration', {}).get('file') or file_identity(config_path)
            config_contract = frozen.get('inference_configuration')
            if run['algorithm'] == 'mint' and config_contract is None and Path(config_path).is_file():
                expected_sha = frozen.get('selected_configuration', {}).get('sha256') or frozen.get('sources', {}).get('configs/training/mint_step2.yaml')
                if expected_sha is None or hashlib.sha256(Path(config_path).read_bytes()).hexdigest() == expected_sha:
                    config_contract = inference_configuration(config_path, model_path)
            identity = {'algorithm': run['algorithm'], 'backend': request.get('backend_version'),
                        'options': run['options'], 'model': model_record or (file_identity(model_path) if model_path else None),
                        'configuration': configuration if run['algorithm'] == 'mint' else None,
                        'inference_configuration': config_contract if run['algorithm'] == 'mint' else None,
                        'origin': 'imported' if run['imported'] else 'native'}
            group = groups.setdefault(digest(identity), {'identity': identity, 'runs': {}})
            if run['sample_id'] in group['runs']:
                raise ValueError('A model variant can have only one prediction per frozen input')
            group['runs'][run['sample_id']] = run
        for key, group in groups.items():
            metrics, failures, paired, total_gt, per_input = [], 0, 0, 0, []
            for sample_id, entry in entries.items():
                reference = self.manager.root / 'runs' / entry['reference_run'] / 'result'
                if file_identity(reference / 'result.json') != entry['reference_identity']:
                    raise ValueError('Frozen reference changed')
                result_metadata(reference)
                total_gt += entry['gt_hand_slots']
                run = group['runs'].get(sample_id)
                if not run or not run['result']:
                    failures += 1
                    per_input.append({'sample_id': sample_id, 'run_id': run['id'] if run else None, 'status': 'missing_or_failed'})
                    continue
                row = paired_metrics(self.manager.root / 'runs' / run['id'] / 'result', reference)
                metrics.append(row); paired += row['paired_hand_slots']
                per_input.append({'sample_id': sample_id, 'run_id': run['id'], 'status': 'completed', 'metrics': row,
                                  'result_identity': file_identity(self.manager.root / 'runs' / run['id'] / 'result/result.json')})
            rows.append({'id': key, **group['identity'], **aggregate_pairs(metrics), 'inputs': per_input,
                'scored_sequences': len(metrics), 'failed_sequences': failures, 'total_sequences': len(entries),
                'gt_geometry_coverage': paired / total_gt if total_gt else None,
                'performance_scope': 'Offline numerical evaluation; no cold-start timing claims'})
        return self.put('evaluation', json_values({'name': str(name)[:200], 'protocol_id': protocol_id, 'split': split,
            'rows': rows, 'status': 'completed' if all(r['failed_sequences'] == 0 for r in rows) else 'partial'}))

    def review(self, *, quality_id, start, end, decision, reviewer, comment=''):
        from .quality import QualityService
        quality = QualityService(self.manager).get(quality_id)
        if decision not in {'accepted', 'rejected', 'needs_label'} or not str(reviewer).strip():
            raise ValueError('Declare reviewer and accepted/rejected/needs_label decision')
        if isinstance(start, bool) or isinstance(end, bool) or not 0 <= int(start) < int(end) <= quality['frames'] or int(start) != start or int(end) != end:
            raise ValueError('Review interval is zero-based [start,end) inside the sample')
        return self.put('review', {'name': quality['run_id'][:12] + ' · ' + decision, 'quality_id': quality_id,
            'run_id': quality['run_id'], 'sample_id': quality['sample_id'], 'start': int(start), 'end': int(end),
            'decision': decision, 'reviewer': str(reviewer).strip()[:200], 'comment': str(comment)[:4000],
            'reviewed_at': time.time(), 'label_role': 'human_reviewed_prediction; not automatic GT'})

    def dataset_version(self, *, name, review_ids):
        if not isinstance(review_ids, list) or not review_ids or len(review_ids) > 1000:
            raise ValueError('Select accepted review records')
        reviews = [self.get(key, 'review') for key in dict.fromkeys(review_ids)]
        if any(row['decision'] != 'accepted' for row in reviews):
            raise ValueError('Dataset versions contain accepted reviews only')
        entries = []
        for row in reviews:
            run = self.manager.run(row['run_id'], summary=True)
            if not run['result']:
                raise ValueError('Reviewed geometry is no longer available')
            request = json.loads(self.manager.row(row['run_id'])['payload'])
            from .native_sources import source_provenance
            origin = source_provenance(self.manager, request['sample_request']['source'])
            source_key = origin['source_key']
            with self.manager.db() as db:
                parents = db.execute('SELECT payload FROM comparisons WHERE instr(payload,?)>0', (row['run_id'],)).fetchall()
            for parent in parents:
                context = json.loads(parent['payload']).get('dataset_context')
                if context:
                    source_key = digest(context['identity']); break
            entries.append({'source_key': source_key, 'official_split': origin['official_split'],
                'review_id': row['id'], 'run_id': row['run_id'], 'sample_id': row['sample_id'],
                'start': row['start'], 'end': row['end'], 'result_identity': file_identity(self.manager.root / 'runs' / row['run_id'] / 'result/result.json'),
                'label_role': row['label_role']})
        result = self.put('dataset', {'name': str(name)[:200], 'entries': entries, 'status': 'reviewed_manifest',
            'training_ready': False, 'reason': 'Curation manifest; label conversion and declared supervision are required before training'})
        atomic_json(self.root / (result['id'] + '-dataset.json'), result)
        return result

    def register_model(self, *, path, name=None, config=None, dataset_id=None, workflow_id=None):
        path = Path(path).expanduser().resolve()
        if path.is_dir():
            path /= 'model.safetensors'
        if not path.is_file() or path.suffix not in {'.safetensors', '.pt', '.pth'}:
            raise ValueError('Select an existing model checkpoint')
        if dataset_id:
            self.get(dataset_id, 'dataset')
        sha = hashlib.sha256()
        before = file_identity(path)
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(8 * 2**20), b''):
                sha.update(chunk)
        if file_identity(path) != before:
            raise ValueError('Checkpoint changed during registration')
        configuration = file_identity(config) if config else None
        if configuration and configuration.get('missing'):
            raise ValueError('Selected configuration is missing')
        return self.put('model', {'name': str(name or path.parent.name)[:200], 'path': str(path), 'identity': before,
            'sha256': sha.hexdigest(), 'config': configuration, 'dataset_id': dataset_id, 'workflow_id': workflow_id,
            'status': 'registered_requires_evaluation'})

    def promote(self, *, model_id, evaluation_id, alias='candidate', max_joint_mm=100., min_coverage=.95,
                baseline_model_id=None, max_regression=.05):
        if not str(alias).replace('_', '').replace('-', '').isalnum() or len(alias) > 64:
            raise ValueError('Choose a short model alias')
        values = [float(max_joint_mm), float(min_coverage), float(max_regression)]
        if not all(math.isfinite(v) for v in values) or values[0] <= 0 or not 0 <= values[1] <= 1 or values[2] < 0:
            raise ValueError('Invalid promotion thresholds')
        model = self.get(model_id, 'model')
        evaluation = self.get(evaluation_id, 'evaluation')
        if file_identity(model['path']) != model['identity']:
            raise ValueError('Registered checkpoint changed')
        matches = [r for r in evaluation['rows'] if r.get('model') == model['identity'] and r['origin'] == 'native']
        if len(matches) != 1:
            raise ValueError('Promotion needs exactly one native evaluation row for this registered checkpoint')
        row = matches[0]
        from .registry import inference_configuration
        configuration = row.get('configuration')
        if configuration and not configuration.get('missing') and row.get('inference_configuration') is not None:
            if file_identity(configuration['path']) != configuration:
                raise ValueError('Evaluated inference configuration changed')
            if inference_configuration(configuration['path'], model['path']) != row.get('inference_configuration'):
                raise ValueError('Checkpoint window metadata or inference settings changed after evaluation')
            if model.get('config') and inference_configuration(model['config']['path'], model['path']) != row.get('inference_configuration'):
                raise ValueError('Registered and evaluated inference configurations differ')
        else:
            raise ValueError('Promotion requires the evaluated inference configuration to remain available')
        if row['failed_sequences'] or not row['scored_sequences'] or row.get('camera_absolute_joint_mm') is None:
            raise ValueError('Promotion requires complete scored predictions on the frozen split')
        if row['camera_absolute_joint_mm'] > values[0] or (row['gt_geometry_coverage'] or 0) < values[1]:
            raise ValueError('Model failed the declared error or coverage gate')
        protocol = self.get(evaluation['protocol_id'], 'protocol')
        for entry in protocol['entries']:
            if entry['split'] != evaluation['split']:
                continue
            reference = self.manager.root / 'runs' / entry['reference_run'] / 'result'
            if file_identity(reference / 'result.json') != entry['reference_identity'] or file_identity(entry['sample_request']['source']['path']) != entry['sample_request']['source']:
                raise ValueError('Frozen protocol source or reference changed after evaluation')
            result_metadata(reference)
        for entry in row['inputs']:
            if file_identity(self.manager.root / 'runs' / entry['run_id'] / 'result/result.json') != entry['result_identity']:
                raise ValueError('Evaluated result changed after evaluation')
            result_metadata(self.manager.root / 'runs' / entry['run_id'] / 'result')
        if baseline_model_id:
            baseline = self.get(baseline_model_id, 'model')
            base_rows = [r for r in evaluation['rows'] if r.get('model') == baseline['identity'] and r['origin'] == 'native']
            if len(base_rows) != 1 or base_rows[0]['failed_sequences'] or base_rows[0].get('camera_absolute_joint_mm') is None:
                raise ValueError('Evaluate the baseline on the identical protocol before comparing regression')
            if row['camera_absolute_joint_mm'] > base_rows[0]['camera_absolute_joint_mm'] * (1 + values[2]):
                raise ValueError('Model exceeds the permitted baseline regression')
        if model.get('dataset_id'):
            training = self.get(model['dataset_id'], 'dataset')
            evaluation_samples = {e['sample_id'] for e in self.get(evaluation['protocol_id'], 'protocol')['entries'] if e['split'] == evaluation['split']}
            test_entries = [e for e in self.get(evaluation['protocol_id'], 'protocol')['entries'] if e['split'] == evaluation['split']]
            train_sources = training_source_keys(self.manager, training)
            if evaluation_samples & {e['sample_id'] for e in training.get('entries', [])} or train_sources & {e['source_key'] for e in test_entries}:
                raise ValueError('Training curation overlaps the evaluation split')
        with self.manager.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT model_id FROM model_aliases WHERE name=?', (alias,)).fetchone()
            db.execute('INSERT OR REPLACE INTO model_aliases(name,model_id,evaluation_id,updated,configuration) VALUES(?,?,?,?,?)',
                       (alias, model_id, evaluation_id, time.time(), json.dumps(configuration or {})))
            gate = {'max_joint_mm': values[0], 'min_coverage': values[1], 'baseline_model_id': baseline_model_id, 'max_regression': values[2]}
            db.execute('INSERT INTO model_alias_events(name,model_id,previous,evaluation_id,at,gate) VALUES(?,?,?,?,?,?)',
                       (alias, model_id, old['model_id'] if old else None, evaluation_id, time.time(), json.dumps(gate)))
        return {'alias': alias, 'model_id': model_id, 'path': model['path'], 'evaluation_id': evaluation_id,
                'effect': 'Selectable checkpoint alias; running workers and the default checkpoint are unchanged'}

    def aliases(self):
        with self.manager.db() as db:
            rows = db.execute('SELECT * FROM model_aliases ORDER BY name').fetchall()
        output = []
        for row in rows:
            record = dict(row)
            model = self.get(row['model_id'], 'model')
            configuration = json.loads(record.pop('configuration'))
            evaluation = self.get(row['evaluation_id'], 'evaluation')
            from .registry import inference_configuration
            tested = [v.get('inference_configuration') for v in evaluation['rows'] if v.get('model') == model['identity'] and v.get('origin') == 'native']
            available = (file_identity(model['path']) == model['identity'] and bool(configuration) and
                         not configuration.get('missing') and file_identity(configuration['path']) == configuration)
            output.append({**record, 'model': model, 'effective_config': configuration,
                'ready': available and inference_configuration(configuration['path'], model['path']) in tested})
        return output

    def rollback(self, alias):
        with self.manager.db() as db:
            rows = db.execute('SELECT * FROM model_alias_events WHERE name=? ORDER BY seq DESC LIMIT 1', (alias,)).fetchall()
        if not rows or not rows[0]['previous']:
            raise ValueError('No prior evaluated model exists for this alias')
        previous = rows[0]['previous']
        with self.manager.db() as db:
            evidence = db.execute('SELECT evaluation_id,gate FROM model_alias_events WHERE name=? AND model_id=? ORDER BY seq DESC LIMIT 1', (alias, previous)).fetchone()
        return self.promote(model_id=previous, evaluation_id=evidence['evaluation_id'], alias=alias, **json.loads(evidence['gate']))

    def experiment(self, *, name, baseline_run, candidate_run, tolerance_mm=.01, minimum_speedup=1.):
        import numpy as np
        if baseline_run == candidate_run or not math.isfinite(float(tolerance_mm)) or float(tolerance_mm) < 0 or not math.isfinite(float(minimum_speedup)) or float(minimum_speedup) <= 0:
            raise ValueError('Select distinct baseline/candidate runs and finite nonnegative tolerance')
        a, b = (self.manager.run(key, summary=True) for key in (baseline_run, candidate_run))
        if not a['result'] or not b['result'] or a['sample_id'] != b['sample_id']:
            raise ValueError('Controlled experiments require two published results on identical inputs')
        ma, aa = load_result(self.manager.root / 'runs' / baseline_run / 'result')
        mb, ab = load_result(self.manager.root / 'runs' / candidate_run / 'result')
        coverage_equal = np.array_equal(aa['source_frame_indices'], ab['source_frame_indices']) and np.array_equal(aa['geometry_valid'], ab['geometry_valid'])
        max_difference = None
        if coverage_equal:
            maximum = 0.
            for start in range(0, len(aa['source_frame_indices']), 2048):
                valid = aa['geometry_valid'][start:start + 2048]
                if valid.any():
                    maximum = max(maximum, float(np.linalg.norm(aa['joints_camera_m'][start:start + 2048][valid] - ab['joints_camera_m'][start:start + 2048][valid], axis=-1).max() * 1000))
            max_difference = maximum
        camera_equal = ('T_world_camera' in aa) == ('T_world_camera' in ab)
        if camera_equal and 'T_world_camera' in aa:
            camera_equal = np.array_equal(aa['pose_valid'], ab['pose_valid']) and np.allclose(aa['T_world_camera'], ab['T_world_camera'], atol=1e-6, equal_nan=True)
        intrinsics_equal = np.array_equal(aa['K'], ab['K'])
        seconds_a, seconds_b = (m.get('timings', {}).get('worker_end_to_end_seconds') for m in (ma, mb))
        performance_comparable = (a['status'] == b['status'] == 'completed' and not a['imported'] and not b['imported']
            and a['algorithm'] == b['algorithm'] and a['options'] == b['options']
            and ma.get('execution', {}).get('cache') == mb.get('execution', {}).get('cache') == 'fresh')
        speedup = seconds_a / seconds_b if performance_comparable and seconds_a and seconds_b else None
        accepted = coverage_equal and camera_equal and intrinsics_equal and max_difference is not None and max_difference <= float(tolerance_mm) and speedup is not None and speedup >= float(minimum_speedup)
        return self.put('experiment', {'name': str(name)[:200], 'baseline_run': baseline_run, 'candidate_run': candidate_run,
            'sample_id': a['sample_id'], 'status': 'passed_declared_gate' if accepted else 'requires_review',
            'coverage_equal': coverage_equal, 'camera_equal': camera_equal, 'intrinsics_equal': intrinsics_equal,
            'maximum_joint_difference_mm': max_difference, 'tolerance_mm': float(tolerance_mm),
            'worker_wall_speedup': speedup, 'minimum_speedup': float(minimum_speedup),
            'performance_comparable': performance_comparable,
            'timing_limit': 'Observed worker wall time; includes cache/model/yield conditions. Repeat cold/warm trials before a throughput claim.',
            'activation': 'No numerical acceleration, fusion or default model is enabled by this record'})

    def deployment(self, *, name, nodes, shared_root, master_address=None, master_port=29500):
        import ipaddress
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 32:
            raise ValueError('Describe 1..32 existing nodes')
        if not str(shared_root).startswith('/') or any(str(shared_root).startswith(path) for path in ('/data1/lizhen_algo/hawor', '/data1/lizhen_algo/ace_ego_hand')):
            raise ValueError('Choose a MINT-owned shared code/data/checkpoint root')
        if isinstance(master_port, bool) or not 1024 <= int(master_port) <= 65535:
            raise ValueError('Choose an unprivileged rendezvous port')
        normalized = []
        for node in nodes:
            if not isinstance(node, dict) or set(node) - {'host', 'gpus', 'rank'}:
                raise ValueError('Node fields: host, gpus, rank')
            host = str(node['host'])
            if not host or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in host):
                raise ValueError('Use a hostname or IP address')
            gpus = node.get('gpus', 1)
            rank = node.get('rank', len(normalized))
            if isinstance(gpus, bool) or int(gpus) != gpus or not 1 <= int(gpus) <= 8 or rank != len(normalized):
                raise ValueError('Nodes require contiguous ranks and 1..8 GPUs each')
            normalized.append({'host': host, 'gpus': int(gpus), 'rank': rank})
        if len({n['host'] for n in normalized}) != len(normalized) or len({n['gpus'] for n in normalized}) != 1:
            raise ValueError('Accelerate profile requires distinct hosts and the same local GPU count')
        address = master_address or normalized[0]['host']
        if address not in [n['host'] for n in normalized]:
            raise ValueError('Master address must name one configured node')
        return self.put('deployment', {'name': str(name)[:200], 'nodes': normalized, 'shared_root': str(shared_root),
            'master_address': address, 'master_port': int(master_port), 'processes': sum(n['gpus'] for n in normalized),
            'status': 'configuration_validated_network_unverified',
            'required_checks': ['Identical MINT revision and isolated dependencies on every node',
                'Shared dataset/weights readable and output owned by MINT', 'NCCL rendezvous and inter-node transport tested',
                'Physical GPU leases obtained independently on every node', 'Recovery from the same complete checkpoint'],
            'launch': [{'host': n['host'], 'arguments': ['-m', 'accelerate.commands.launch', '--multi_gpu',
                '--num_machines', str(len(normalized)), '--num_processes', str(sum(v['gpus'] for v in normalized)),
                '--machine_rank', str(n['rank']), '--main_process_ip', address, '--main_process_port', str(master_port)]} for n in normalized],
            'effect': 'Validated manifest only; no SSH, cloud resources or distributed workers are launched'})
