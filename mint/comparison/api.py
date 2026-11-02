"""The comparison workspace is mounted beside the original MINT interface."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from flask import Blueprint, abort, jsonify, redirect, request, send_file, url_for

from .contracts import json_values, load_result
from .jobs import ComparisonManager
from .registry import ALGORITHMS, backend_spec, root_for


def comparison_blueprint(legacy_store=None, dataset_analysis=None):
    blueprint = Blueprint('comparison', __name__, static_folder='web', static_url_path='/comparison-assets')
    lock = threading.Lock()
    manager_instance = []
    exports_instance = []
    collection_instance = []
    tool_instance = []
    workspace_instance = []
    research_instance = []
    workflow_instance = []
    study_instance = []
    def manager():
        with lock:
            if not manager_instance:
                manager_instance.append(ComparisonManager())
            return manager_instance[0]

    def exports():
        from .exports import ComparisonExports
        value = manager()
        with lock:
            if not exports_instance:
                exports_instance.append(ComparisonExports(value))
            return exports_instance[0]

    def collections():
        from .collections import CollectionManager
        value = manager()
        with lock:
            if not collection_instance:
                collection_instance.append(CollectionManager(value))
            return collection_instance[0]

    def tools():
        from .tools import RenderTools
        value = manager()
        with lock:
            if not tool_instance:
                tool_instance.append(RenderTools(value))
            return tool_instance[0]

    def workspace():
        from .workspace import WorkspaceServices
        values = (manager(), collections(), tools())
        with lock:
            if not workspace_instance:
                workspace_instance.append(WorkspaceServices(*values, legacy_store, dataset_analysis))
            return workspace_instance[0]

    def research():
        from .research import ResearchRegistry
        value = manager()
        with lock:
            if not research_instance:
                research_instance.append(ResearchRegistry(value))
            return research_instance[0]

    def workflows():
        from .workflows import WorkflowManager
        value, registry, hub = manager(), research(), workspace()
        with lock:
            if not workflow_instance:
                workflow_instance.append(WorkflowManager(value, registry))
                hub.workflows = workflow_instance[0]
            return workflow_instance[0]

    def studies():
        from .studies import StudyManager
        value, registry, jobs, hub = manager(), research(), workflows(), workspace()
        with lock:
            if not study_instance:
                study_instance.append(StudyManager(value, registry, jobs))
                hub.studies = study_instance[0]
            return study_instance[0]

    @blueprint.record_once
    def register_services(state):
        # The viewer calls this after construction. Recovery must not depend on a
        # browser opening a task page after a process restart.
        state.app.extensions['mint_comparison_start'] = lambda: (manager(), collections(), tools(), exports(), workspace(), workflows(), studies(), workspace().start())

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object')
        return value

    @blueprint.after_request
    def compress_json(response):
        # Media/Range responses stay byte-addressable. Only complete JSON is compressed.
        if response.is_json:
            response.vary.add('Accept-Encoding')
        if response.is_json and not response.direct_passthrough and request.accept_encodings['gzip'] > 0 and not response.headers.get('Content-Encoding'):
            raw = response.get_data()
            if len(raw) >= 4096:
                import gzip
                packed = gzip.compress(raw, compresslevel=1, mtime=0)
                if len(packed) < len(raw):
                    response.set_data(packed)
                    response.headers['Content-Encoding'] = 'gzip'
                    response.vary.add('Accept-Encoding')
                    response.headers.pop('ETag', None)
        return response

    @blueprint.errorhandler(ValueError)
    @blueprint.errorhandler(TypeError)
    @blueprint.errorhandler(OverflowError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @blueprint.errorhandler(KeyError)
    @blueprint.errorhandler(FileNotFoundError)
    def missing(error):
        return jsonify(error=str(error)), 404

    @blueprint.get('/compare')
    def page():
        return send_file(Path(__file__).parent / 'web/index.html')

    @blueprint.get('/compare/')
    def canonical_page():
        return redirect(url_for('comparison.page') + ('?' + request.query_string.decode('latin-1') if request.query_string else ''))

    @blueprint.get('/api/algorithms')
    def algorithms():
        from .registry import parameter_schema
        specs = [{**backend_spec(name), 'parameters': parameter_schema(name)} for name in ALGORITHMS]
        # The read-only capability inventory must not load frameworks or create workers.
        return jsonify(algorithms=[{k: v for k, v in value.items() if k not in {'identity', 'python'}} for value in specs])

    @blueprint.get('/api/comparison/examples')
    def examples():
        candidates = [('HaWoR 官方样例 · 121 帧', root_for('hawor_full') / 'example/video_0.mp4'),
                      ('已部署 720 帧双目样例（选择左眼）', root_for('mint') / 'artifacts/long_video_api_smoke_20261029/input_stereo_720.mp4'),
                      ('ACE 官方样例', root_for('ace_kfree') / 'examples/original4.mp4')]
        return jsonify(examples=[{'label': label, 'path': str(path)} for label, path in candidates if path.is_file()])

    @blueprint.get('/api/comparisons')
    def history():
        return jsonify(comparisons=manager().history(request.args.get('limit', 30), request.args.get('offset', 0)))

    @blueprint.get('/api/comparison/default-example')
    def default_example():
        from .default_example import get_default_example
        return jsonify(get_default_example(manager(), research()))

    @blueprint.post('/api/comparisons')
    def create():
        data = body()
        allowed = {'source', 'selections', 'view', 'target_fps', 'max_frames', 'start_frame', 'camera', 'title', 'dataset', 'execution'}
        if set(data) - allowed:
            raise ValueError('Unknown comparison fields')
        from .preflight import inspect_request
        check = inspect_request(manager(), **data)
        if not check['ready']:
            return jsonify(error='；'.join(check['errors']), preflight=check), 400
        return jsonify(manager().create(**data)), 201

    @blueprint.post('/api/comparison/preflight')
    def comparison_preflight():
        from .preflight import inspect_request
        data = body()
        if set(data) - {'source', 'selections', 'view', 'target_fps', 'max_frames', 'start_frame', 'camera', 'title', 'dataset', 'execution'}:
            raise ValueError('Unknown preflight fields')
        return jsonify(inspect_request(manager(), **data))

    @blueprint.get('/api/comparison/tasks/<kind>/<task_id>')
    def task_detail(kind, task_id):
        workspace().refresh_index()
        return jsonify(workspace().index.detail(kind, task_id))

    @blueprint.get('/api/comparison/registry/<kind>')
    def registry_page(kind):
        if kind == 'artifact':
            research().index_artifacts()
        return jsonify(research().page(kind, query=request.args.get('query', ''),
            offset=request.args.get('offset', 0), limit=request.args.get('limit', 50)))

    @blueprint.get('/api/comparison/registry/record/<record_id>')
    def registry_record(record_id):
        return jsonify(research().get(record_id))

    @blueprint.post('/api/comparison/registry/<kind>')
    def registry_create(kind):
        methods = {'artifact': 'register_raw', 'protocol': 'protocol', 'evaluation': 'evaluate', 'review': 'review',
                   'dataset': 'dataset_version', 'model': 'register_model', 'experiment': 'experiment', 'deployment': 'deployment'}
        if kind not in methods:
            raise ValueError('Unknown registry operation')
        return jsonify(json_values(getattr(research(), methods[kind])(**body()))), 201

    @blueprint.post('/api/comparison/registry/artifacts/<record_id>/import')
    def registry_import(record_id):
        return jsonify(research().import_artifact(record_id)), 201

    @blueprint.get('/api/comparison/models/aliases')
    def model_aliases():
        return jsonify(aliases=research().aliases())

    @blueprint.post('/api/comparison/models/promote')
    def model_promote():
        return jsonify(research().promote(**body()))

    @blueprint.post('/api/comparison/models/rollback')
    def model_rollback():
        data = body()
        if set(data) != {'alias'}:
            raise ValueError('Choose one alias to roll back')
        return jsonify(research().rollback(data['alias']))

    @blueprint.post('/api/comparison/quality')
    def create_quality():
        from .quality import QualityService
        data = body()
        if set(data) - {'run_id', 'reference_run', 'joint_mm', 'acceleration'} or not isinstance(data.get('run_id'), str) or not data['run_id'].strip():
            raise ValueError('Choose one published prediction and known quality settings')
        if data.get('reference_run') is not None and not isinstance(data['reference_run'], str):
            raise ValueError('Choose an optional published reference result')
        return jsonify(QualityService(manager()).create(**data)), 201

    @blueprint.get('/api/comparison/quality/<quality_id>')
    def get_quality(quality_id):
        from .quality import QualityService
        return jsonify(QualityService(manager()).get(quality_id,
            offset=request.args.get('offset', 0), limit=request.args.get('limit', 50)))

    @blueprint.get('/api/comparison/quality/<quality_id>/download')
    def download_quality(quality_id):
        from .quality import QualityService
        service = QualityService(manager())
        service.get(quality_id, limit=1)
        return send_file(service.root / (quality_id + '.npz'), as_attachment=True, download_name='quality-' + quality_id[:12] + '.npz')

    @blueprint.post('/api/comparison/workflows/preflight')
    def workflow_preflight():
        return jsonify(workflows().preflight(**body()))

    @blueprint.get('/api/comparison/native/catalog')
    def native_catalog():
        from .native_sources import dataset_catalog
        return jsonify(dataset_catalog(request.args.get('root', '/dataset')))

    @blueprint.get('/api/comparison/native/sequences')
    def native_sequences():
        from .native_sources import sequences
        return jsonify(sequences(request.args['dataset'], request.args['root'], group=request.args.get('group', ''),
            split=request.args.get('split', 'test'), offset=request.args.get('offset', 0), limit=request.args.get('limit', 50)))

    @blueprint.post('/api/comparison/native/<dataset_id>/compare')
    def native_compare(dataset_id):
        data = body()
        if set(data) - {'selections', 'calibration', 'title', 'execution'}:
            raise ValueError('Unknown native comparison fields')
        calibration = data.get('calibration', False)
        if not isinstance(calibration, bool):
            raise ValueError('Calibration must be an explicit boolean')
        record = research().get(dataset_id, 'dataset')
        native = record.get('native')
        if not native:
            raise ValueError('Select a prepared native dataset version')
        from .native_sources import prepare_native
        native = prepare_native(native['plan'], Path(native['reference']).parent)
        shared = {'source': native['source'], 'view': 'full', 'target_fps': 0,
                  'camera': native['camera'] if calibration else None}
        from .preflight import inspect_request
        check = inspect_request(manager(), **shared, selections=data['selections'], execution=data.get('execution'))
        if not check['ready']:
            return jsonify(error='；'.join(check['errors']), preflight=check), 400
        comparison = manager().create(**shared, selections=data['selections'], execution=data.get('execution'),
            title=data.get('title') or record['name'])
        reference = manager().create(**shared, selections=[{'algorithm': 'reference'}],
            import_entry={'path': native['reference'], 'format': 'normalized_npz', 'frame_map': list(range(native['frames'])),
                          'declared_source': json.dumps({'native_dataset': native['plan']['dataset'], 'split': native['plan']['split'],
                              'label_role': native['details']['label_role'], 'version': native['id'], 'units': 'm'})},
            comparison_key='native-reference:' + native['id'] + ':' + str(calibration))
        return jsonify(manager().attach(comparison['id'], reference['runs'][0]['id'])), 201

    @blueprint.post('/api/comparison/workflows')
    def create_workflow():
        return jsonify(workflows().create(**body())), 201

    @blueprint.get('/api/comparison/workflows/<workflow_id>')
    def get_workflow(workflow_id):
        return jsonify(workflows().get(workflow_id))

    @blueprint.get('/api/comparison/workflows/<workflow_id>/curves')
    def workflow_curves(workflow_id):
        from .training_curves import read_curves
        return jsonify(read_curves(workflows(), workflow_id, maximum=request.args.get('maximum', 800)))

    @blueprint.post('/api/comparison/studies')
    def create_study():
        return jsonify(studies().create(**body())), 201

    @blueprint.get('/api/comparison/studies/<study_id>')
    def study_state(study_id):
        return jsonify(studies().get(study_id))

    @blueprint.post('/api/comparison/studies/<study_id>/<action>')
    def study_action(study_id, action):
        if action not in {'cancel', 'resume'}:
            raise ValueError('Choose cancel/resume')
        return jsonify(getattr(studies(), action)(study_id))

    @blueprint.get('/api/comparison/runs/<run_id>/breakdown')
    def breakdown(run_id):
        from .diagnostics import run_breakdown
        return jsonify(run_breakdown(manager(), run_id, request.args['reference_run']))

    @blueprint.get('/api/comparison/runs/<run_id>/annotation-context')
    def annotation_context(run_id):
        from .annotations import AnnotationService
        return jsonify(AnnotationService(manager(), research()).context(run_id, int(request.args.get('frame', 0))))

    @blueprint.post('/api/comparison/annotations')
    def save_annotation():
        from .annotations import AnnotationService
        return jsonify(AnnotationService(manager(), research()).save(**body())), 201

    @blueprint.get('/api/comparison/annotations/history')
    def annotation_history():
        from .annotations import AnnotationService
        return jsonify(AnnotationService(manager(), research()).history(request.args['run_id'], int(request.args['frame']),
            limit=request.args.get('limit', 50)))

    @blueprint.get('/api/comparison/runs/<run_id>/image')
    def source_image(run_id):
        import io
        import numpy as np
        from PIL import Image
        from decord import VideoReader
        from mint.runtime import file_identity
        from .prepare import select_view
        run = manager().run(run_id, summary=True)
        sample = json.loads((manager().root / 'inputs' / run['sample_id'] / 'sample.json').read_text())
        frame = int(request.args.get('frame', 0))
        if not 0 <= frame < sample['frames']:
            raise ValueError('Frame escapes input timeline')
        source = sample['request']['source']
        if file_identity(source['path']) != source:
            raise ValueError('Source video changed')
        reader = VideoReader(source['path'], num_threads=1)
        rgb = select_view(reader[int(sample['source_frame_indices'][frame])].asnumpy()[None], sample['request']['view'])[0]
        if rgb.shape[:2] != (sample['height'], sample['width']):
            raise ValueError('Image grid changed')
        buffer = io.BytesIO(); Image.fromarray(np.asarray(rgb)).save(buffer, format='PNG'); buffer.seek(0)
        return send_file(buffer, mimetype='image/png', max_age=0)

    @blueprint.post('/api/comparison/workflows/<workflow_id>/<action>')
    def workflow_action(workflow_id, action):
        if action not in {'cancel', 'resume'}:
            raise ValueError('Unknown workflow action')
        return jsonify(getattr(workflows(), action)(workflow_id))

    @blueprint.get('/api/comparison/workflows/<workflow_id>/log')
    def workflow_log(workflow_id):
        workflows().get(workflow_id)
        path = workflows().root / workflow_id / 'worker.log'
        if not path.exists():
            return jsonify(text='Worker has not started')
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 24000))
            return jsonify(text=stream.read().decode(errors='replace'))

    @blueprint.get('/api/comparison/dataset')
    def dataset_catalog():
        from .datasets import catalog
        return jsonify(catalog(request.args.get('root', ''), request.args.get('camera'),
            request.args.get('offset', 0), request.args.get('limit', 100), request.args.get('episode')))

    @blueprint.get('/api/comparison/topology')
    def topology():
        from mint.visualization.draw import get_faces
        try:
            right, left = get_faces()
        except (OSError, ImportError) as error:
            return jsonify(error=str(error)), 503
        return jsonify(vertices=778, hands={'left': left.tolist(), 'right': right.tolist()}, topology='MANO778')

    @blueprint.get('/api/comparison/workspace')
    def workspace_index():
        workflows()
        return jsonify(json_values(workspace().tasks(limit=request.args.get('limit', 100),
            offset=request.args.get('offset', 0), query=request.args.get('query', ''), status=request.args.get('status', 'all'), kind=request.args.get('kind', 'all'))))

    @blueprint.get('/api/comparison/capabilities')
    def workspace_capabilities():
        from .tools import capabilities
        cloud = {'ready': False, 'reason': '请在原工作台配置 Aliyun 运行参数'}
        benchmark = None
        if legacy_store is not None:
            benchmark = legacy_store.benchmark_capabilities()
            settings = legacy_store.benchmark_aliyun_defaults().get('aliyun', {})
            required = ('workspace_id', 'resource_id', 'cpfs_uri', 'image')
            cloud['ready'] = all(settings.get(key) and 'REPLACE_ME' not in str(settings[key]) and 'example.com' not in str(settings[key]) for key in required)
        return jsonify(render=capabilities(), benchmark=benchmark, cloud=cloud,
            legacy=legacy_store is not None, data_analysis=dataset_analysis is not None)

    @blueprint.post('/api/comparison/diagnostics')
    def workspace_diagnostics():
        import os
        import subprocess
        import sys
        from .registry import PROJECT
        data = body()
        if set(data) - {'profile'} or data.get('profile', 'full') not in {'train', 'data', 'full', 'inference'}:
            raise ValueError('Choose a known diagnostic profile')
        script = ('import contextlib,io,json;from dataclasses import asdict;from mint.doctor import run_checks;'
                  'capture=io.StringIO();\nwith contextlib.redirect_stdout(capture): checks=run_checks(__import__("sys").argv[1])\n'
                  'print(json.dumps([asdict(check) for check in checks]))')
        env = os.environ.copy()
        env.update(PYTHONPATH=str(PROJECT), PYTHONDONTWRITEBYTECODE='1', CUDA_VISIBLE_DEVICES='', MINT_MANO_DEVICE='cpu',
                   MPLCONFIGDIR=str(PROJECT / '.cache/diagnostics/matplotlib'), OMP_NUM_THREADS='2')
        from .workflows import owned_environment
        requested = data.get('profile', 'full')
        profiles = ['inference', 'train', 'data'] if requested == 'full' else [requested]
        checks, environments = [], []
        for profile in profiles:
            python = owned_environment(profile if profile == 'train' else 'pipeline') if profile != 'inference' else Path(sys.executable)
            installed = python.is_file()
            try:
                result = subprocess.run([str(python) if installed else sys.executable, '-c', script, profile], cwd=PROJECT,
                                        env=env, capture_output=True, text=True, timeout=90)
            except subprocess.TimeoutExpired:
                return jsonify(error='Diagnostic process exceeded 90 seconds; no inference or training was started'), 504
            if result.returncode:
                return jsonify(error=result.stderr[-2000:] or 'Diagnostic process failed'), 400
            group = [{**row, 'profile': profile} for row in json.loads(result.stdout)]
            if not installed:
                group.append({'name': 'isolated ' + profile + ' environment', 'ok': False,
                              'detail': str(python), 'required': True, 'profile': profile})
            environments.append({'profile': profile, 'python': str(python), 'installed': installed,
                                 'ready': all(c['ok'] for c in group if c['required'])})
            checks.extend(group)
        environment_ready = all(e['ready'] for e in environments)
        input_checks = []
        if 'train' in profiles:
            from dataclasses import asdict
            from mint.readiness import training_inputs
            for config in ('configs/training/mint_step1.yaml', 'configs/training/mint_step2.yaml'):
                input_checks.extend({**asdict(check), 'configuration': config, 'profile': 'train'} for check in training_inputs(config))
        checks.extend(input_checks)
        return jsonify(checks=checks, environments=environments, environment_ready=environment_ready,
            inputs_ready=all(c['ok'] for c in input_checks if c['required']),
            ready=all(check['ok'] for check in checks if check['required']))

    @blueprint.get('/api/comparison/legacy-results')
    def legacy_results():
        return jsonify(results=workspace().cached_predictions())

    @blueprint.post('/api/comparison/legacy-results/import')
    def legacy_import():
        data = body()
        if set(data) != {'id'}:
            raise ValueError('Select one existing workspace prediction')
        return jsonify(workspace().import_prediction(data['id'])), 201

    @blueprint.get('/api/comparison/scan')
    def scan_collection():
        from .collections import scan_videos
        return jsonify(scan_videos(request.args.get('path', ''), request.args.get('recursive') == '1', request.args.get('limit', 1000)))

    @blueprint.post('/api/comparison/collections')
    def create_collection():
        data = body()
        if set(data) - {'kind', 'items', 'selections', 'options', 'title', 'preflight_id'}:
            raise ValueError('Unknown batch/evaluation fields')
        receipt = data.pop('preflight_id', None)
        if receipt is not None and (not isinstance(receipt, str) or len(receipt) != 64 or any(c not in '0123456789abcdef' for c in receipt)):
            raise ValueError('Invalid batch preflight_id')
        from .preflight import inspect_collection
        check = inspect_collection(manager(), **data)
        if receipt is not None and receipt != check['preflight_id']:
            return jsonify(error='输入文件或算法条件已变化，请重新预检', preflight=check), 400
        if not check['ready']:
            return jsonify(error='；'.join(check['errors'][:20]), preflight=check), 400
        return jsonify(collections().create(**data, _preflight=check)), 201

    @blueprint.post('/api/comparison/collections/preflight')
    def collection_preflight():
        from .preflight import inspect_collection
        data = body()
        if set(data) - {'kind', 'items', 'selections', 'options', 'title'}:
            raise ValueError('Unknown batch/evaluation fields')
        return jsonify(inspect_collection(manager(), **data))

    @blueprint.get('/api/comparison/collections')
    def collection_history():
        return jsonify(collections=collections().history(request.args.get('limit', 100)))

    @blueprint.get('/api/comparison/collections/<collection_id>')
    def collection_status(collection_id):
        return jsonify(json_values(collections().get(collection_id)))

    @blueprint.post('/api/comparison/collections/<collection_id>/<action>')
    def collection_action(collection_id, action):
        if action not in {'cancel', 'resume'}:
            raise ValueError('Unknown collection action')
        return jsonify(getattr(collections(), action)(collection_id))

    @blueprint.get('/api/comparison/collections/<collection_id>/report')
    def collection_report(collection_id):
        report = collections().report(collection_id)
        if request.args.get('format') == 'csv':
            from .evaluation import report_csv
            from flask import Response
            return Response(report_csv(report), mimetype='text/csv',
                headers={'Content-Disposition': f'attachment; filename=evaluation-{collection_id[:12]}.csv'})
        return jsonify(report)

    @blueprint.post('/api/comparison/runs/<run_id>/tools')
    def start_render_tool(run_id):
        data = body()
        if set(data) - {'kind', 'space', 'start', 'end', 'width', 'include_absent', 'resource_policy'}:
            raise ValueError('Unknown rendering settings')
        return jsonify(tools().start(run_id, **data)), 202

    @blueprint.get('/api/comparison/runs/<run_id>/tools/options')
    def render_tool_options(run_id):
        return jsonify(tools().options(run_id))

    @blueprint.get('/api/comparison/tools/<job_id>')
    def render_status(job_id):
        return jsonify(tools().status(job_id))

    @blueprint.post('/api/comparison/tools/<job_id>/cancel')
    def cancel_render(job_id):
        return jsonify(tools().cancel(job_id))

    @blueprint.get('/api/comparison/tools/<job_id>/video')
    def render_video(job_id):
        state = tools().status(job_id)
        if state['status'] != 'completed':
            raise ValueError('Render video is unavailable: ' + state['status'])
        return send_file(tools().root / job_id / 'video.mp4', mimetype='video/mp4', conditional=True)

    @blueprint.get('/api/comparison/tools/<job_id>/mapping')
    def render_mapping(job_id):
        state = tools().status(job_id)
        if state['status'] != 'completed':
            raise ValueError('Rendering is incomplete')
        return send_file(tools().root / job_id / 'mapping.json', mimetype='application/json', as_attachment=True)

    @blueprint.get('/api/comparison/runs/<run_id>/parent')
    def parent_comparison(run_id):
        manager().row(run_id)
        with manager().db() as db:
            rows = db.execute('SELECT id,payload FROM comparisons ORDER BY created DESC').fetchall()
        parents = [row['id'] for row in rows if run_id in json.loads(row['payload'])['run_ids']]
        return jsonify(comparisons=parents)

    @blueprint.post('/api/comparison/import')
    def import_result():
        data = body()
        allowed = {'source', 'selections', 'view', 'target_fps', 'max_frames', 'start_frame', 'camera', 'title', 'import_entry', 'execution'}
        if set(data) - allowed or 'import_entry' not in data or len(data.get('selections', [])) != 1:
            raise ValueError('Import one explicit algorithm result with an import_entry and frame_map')
        return jsonify(manager().create(**data)), 201

    @blueprint.get('/api/comparisons/<comparison_id>')
    def get_comparison(comparison_id):
        from .results import common_world_origin
        value = manager().comparison(comparison_id)
        summaries = workspace().index.inference_summaries(value['runs'])
        for row in value['runs']:
            row['task_summary'] = summaries.get(row['id'])
        completed = [manager().root / 'runs' / row['id'] / 'result' for row in value['runs'] if row['result']]
        value['common_world_origin_source_frame'] = common_world_origin(completed)
        return jsonify(json_values(value))

    @blueprint.get('/api/comparison/runs/<run_id>')
    def run(run_id):
        return jsonify(json_values(manager().run(run_id)))

    @blueprint.post('/api/comparison/runs/<run_id>/cancel')
    def cancel(run_id):
        return jsonify(manager().cancel(run_id))

    @blueprint.post('/api/comparison/runs/<run_id>/resume')
    def resume(run_id):
        return jsonify(manager().resume(run_id))

    @blueprint.post('/api/comparisons/<comparison_id>/combine')
    def combine(comparison_id):
        data = body()
        return jsonify(manager().combine(comparison_id, data['hand_run'], data['camera_run']))

    @blueprint.post('/api/comparisons/<comparison_id>/attach')
    def attach(comparison_id):
        return jsonify(manager().attach(comparison_id, body()['run_id']))

    @blueprint.get('/api/comparison/runs/<run_id>/frames')
    def frames(run_id):
        from .results import frame_payload
        row = manager().run(run_id)
        if not row['result']:
            raise ValueError('This run has no validated result yet')
        sample = json.loads((manager().root / 'inputs' / row['sample_id'] / 'sample.json').read_text())
        origin = request.args.get('origin')
        value = frame_payload(manager().root / 'runs' / run_id / 'result', sample,
            int(request.args.get('start', 0)), int(request.args.get('count', 64)),
            vertices=request.args.get('vertices') == '1', origin_source_frame=None if origin is None else int(origin))
        return jsonify(value)

    @blueprint.get('/api/comparison/runs/<run_id>/trajectory')
    def trajectory(run_id):
        from .results import trajectory
        row = manager().run(run_id)
        if not row['result']:
            raise ValueError('Run not complete')
        origin = request.args.get('origin')
        return jsonify(trajectory(manager().root / 'runs' / run_id / 'result', origin_source_frame=None if origin is None else int(origin)))

    @blueprint.get('/api/comparison/runs/<run_id>/parameters')
    def parameters(run_id):
        from .parameters import frame_parameters
        origin = request.args.get('origin')
        return jsonify(frame_parameters(manager(), run_id, request.args.get('frame', 0),
            origin_source_frame=origin))

    @blueprint.get('/api/comparisons/<comparison_id>/metrics')
    def metrics(comparison_id):
        from .results import paired_metrics
        value = manager().comparison(comparison_id)
        runs = [row for row in value['runs'] if row['result']]
        runs.sort(key=lambda row: row['result'].get('reference_role') == 'user_declared_ground_truth')
        return jsonify(metrics=[paired_metrics(manager().root / 'runs' / a['id'] / 'result', manager().root / 'runs' / b['id'] / 'result')
                                for index, a in enumerate(runs) for b in runs[index + 1:]])

    @blueprint.get('/comparison-media/<sample_id>.mp4')
    def input_video(sample_id):
        if len(sample_id) != 64 or any(c not in '0123456789abcdef' for c in sample_id):
            abort(404)
        path = manager().root / 'inputs' / sample_id / 'input.mp4'
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype='video/mp4', conditional=True)

    @blueprint.get('/api/comparison/runs/<run_id>/log')
    def log(run_id):
        manager().row(run_id)
        path = manager().root / 'runs' / run_id / 'worker.log'
        if not path.exists():
            return jsonify(text='Worker has not started')
        logs=[path,*sorted((path.parent/'native').glob('*.log'),key=lambda p:p.stat().st_mtime,reverse=True)[:16]]
        parts=[]
        for item in logs:
            with item.open('rb') as stream:
                stream.seek(max(0,item.stat().st_size-(16000 if item==path else 4000)))
                parts.append(f'[{item.name}]\n'+stream.read().decode(errors='replace'))
        return jsonify(text='\n\n'.join(parts))

    @blueprint.get('/api/comparison/runs/<run_id>/manifest')
    def manifest(run_id):
        row = manager().run(run_id)
        if not row['result']:
            raise ValueError('Run has no validated manifest')
        return send_file(manager().root / 'runs' / run_id / 'result/result.json', mimetype='application/json',
                         as_attachment=True, download_name=run_id[:12] + '-manifest.json')

    @blueprint.get('/api/comparison/runs/<run_id>/download')
    def download(run_id):
        row = manager().run(run_id)
        if not row['result']:
            raise ValueError('Run not complete')
        path = manager().root / 'runs' / run_id / 'result'
        from .exports import geometry_archive
        archive = geometry_archive(path, manager().root / 'geometry-downloads')
        return send_file(archive, mimetype='application/zip', as_attachment=True, download_name=run_id[:12] + '-geometry.zip')

    @blueprint.post('/api/comparisons/<comparison_id>/export')
    def start_export(comparison_id):
        options = body()
        if set(options) - {'include_absent', 'run_ids', 'geometry', 'baseline_run'}:
            raise ValueError('Unknown export display options')
        return jsonify(exports().start(comparison_id, **options)), 202

    @blueprint.get('/api/comparison/exports/<export_id>')
    def export_status(export_id):
        return jsonify(exports().status(export_id))

    @blueprint.post('/api/comparison/exports/<export_id>/cancel')
    def export_cancel(export_id):
        return jsonify(exports().cancel(export_id))

    @blueprint.post('/api/comparison/exports/<export_id>/resume')
    def export_resume(export_id):
        return jsonify(exports().resume(export_id))

    @blueprint.get('/api/comparison/exports/<export_id>/download')
    def export_download(export_id):
        state = exports().status(export_id)
        if state['status'] != 'completed':
            raise ValueError('Export is not available: ' + state['status'])
        return send_file(exports().directory / (export_id + '.mp4'), mimetype='video/mp4',
                         as_attachment=True, download_name='mint-comparison-' + export_id[:12] + '.mp4')

    return blueprint
