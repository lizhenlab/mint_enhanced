"""The comparison workspace is mounted beside the original MINT interface."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from flask import Blueprint, abort, jsonify, redirect, request, send_file

from .contracts import json_values, load_result
from .jobs import ComparisonManager
from .registry import ALGORITHMS, backend_spec, root_for


def comparison_blueprint():
    blueprint = Blueprint('comparison', __name__, static_folder='web', static_url_path='/comparison-assets')
    lock = threading.Lock()
    manager_instance = []
    exports_instance = []
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

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object')
        return value

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
        return jsonify(comparisons=manager().history(request.args.get('limit', 30)))

    @blueprint.post('/api/comparisons')
    def create():
        data = body()
        allowed = {'source', 'selections', 'view', 'target_fps', 'max_frames', 'start_frame', 'camera', 'title'}
        if set(data) - allowed:
            raise ValueError('Unknown comparison fields')
        return jsonify(manager().create(**data)), 201

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
        if set(options) - {'include_absent'}:
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
