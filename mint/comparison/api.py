"""The comparison workspace is mounted beside the original MINT interface."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from flask import Blueprint, abort, jsonify, redirect, request, send_file

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
        return WorkspaceServices(manager(), collections(), tools(), legacy_store, dataset_analysis)

    @blueprint.record_once
    def register_services(state):
        # The viewer calls this after construction. Recovery must not depend on a
        # browser opening a task page after a process restart.
        state.app.extensions['mint_comparison_start'] = lambda: (manager(), collections(), tools())

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
    def missing(error):
        return jsonify(error=str(error)), 404

    @blueprint.get('/compare')
    def page():
        return send_file(Path(__file__).parent / 'web/index.html')

    @blueprint.get('/compare/')
    def canonical_page():
        return redirect('/compare' + ('?' + request.query_string.decode('ascii') if request.query_string else ''))

    @blueprint.get('/api/algorithms')
    def algorithms():
        specs = [backend_spec(name) for name in ALGORITHMS]
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

    @blueprint.post('/api/comparisons')
    def create():
        data = body()
        allowed = {'source', 'selections', 'view', 'target_fps', 'max_frames', 'start_frame', 'camera', 'title', 'dataset'}
        if set(data) - allowed:
            raise ValueError('Unknown comparison fields')
        return jsonify(manager().create(**data)), 201

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
        return jsonify(json_values(workspace().tasks(limit=request.args.get('limit', 100),
            offset=request.args.get('offset', 0), query=request.args.get('query', ''), status=request.args.get('status', 'all'))))

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
        try:
            result = subprocess.run([sys.executable, '-c', script, data.get('profile', 'full')], cwd=PROJECT,
                                    env=env, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            return jsonify(error='Diagnostic process exceeded 90 seconds; no inference or training was started'), 504
        if result.returncode:
            return jsonify(error=result.stderr[-2000:] or 'Diagnostic process failed'), 400
        checks = json.loads(result.stdout)
        if data.get('profile') == 'train':
            from dataclasses import asdict
            from mint.readiness import training_inputs
            for config in ('configs/training/mint_step1.yaml', 'configs/training/mint_step2.yaml'):
                checks.extend({**asdict(check), 'configuration': config} for check in training_inputs(config))
        return jsonify(checks=checks, ready=all(check['ok'] for check in checks if check['required']))

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
        if set(data) - {'kind', 'items', 'selections', 'options', 'title'}:
            raise ValueError('Unknown batch/evaluation fields')
        return jsonify(collections().create(**data)), 201

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
        return jsonify(tools().start(run_id, **body())), 202

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
        allowed = {'source', 'selections', 'view', 'target_fps', 'max_frames', 'start_frame', 'camera', 'title', 'import_entry'}
        if set(data) - allowed or 'import_entry' not in data or len(data.get('selections', [])) != 1:
            raise ValueError('Import one explicit algorithm result with an import_entry and frame_map')
        return jsonify(manager().create(**data)), 201

    @blueprint.get('/api/comparisons/<comparison_id>')
    def get_comparison(comparison_id):
        from .results import common_world_origin
        value = manager().comparison(comparison_id)
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

    @blueprint.get('/api/comparison/exports/<export_id>/download')
    def export_download(export_id):
        state = exports().status(export_id)
        if state['status'] != 'completed':
            raise ValueError('Export is not available: ' + state['status'])
        return send_file(exports().directory / (export_id + '.mp4'), mimetype='video/mp4',
                         as_attachment=True, download_name='mint-comparison-' + export_id[:12] + '.mp4')

    return blueprint
