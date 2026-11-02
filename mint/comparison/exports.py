"""Bounded CPU exports. Full numerical results remain independent of display size."""
from __future__ import annotations

import atexit
import json
import math
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, digest, file_identity
from mint.inference.cache_budget import CacheBudget, file_lock, _token
from .contracts import CONNECTIONS, load_result


def geometry_archive(result_path, directory):
    """Build on disk; a long sequence is never collected into a BytesIO object."""
    result_path, directory = Path(result_path), Path(directory)
    metadata, arrays = load_result(result_path)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (digest({'result': file_identity(result_path / 'result.json')}) + '.zip')
    with file_lock(destination.with_suffix('.lock')):
        if destination.is_file():
            return destination
        expected = sum((result_path / (key + '.npy')).stat().st_size for key in arrays) + 2**20
        budget = CacheBudget(directory, pattern='*.zip', max_gib=float(os.environ.get('MINT_COMPARISON_ZIP_MAX_GIB', '16')))
        with budget.reserve(destination, int(expected * 1.02)):
            temporary = destination.with_name(destination.stem + f'.partial-{os.getpid()}.zip')
            try:
                with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as output:
                    output.writestr('result.json', json.dumps(metadata, ensure_ascii=False, indent=2))
                    for name in arrays:
                        output.write(result_path / (name + '.npy'), name + '.npy')
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    return destination


def overlay_frame(frame_rgb, arrays, source_frame, size, label, *, include_absent=False, geometry='skeleton', baseline=None):
    import cv2
    width, height = size
    panel = np.full((height + 58, width, 3), (24, 28, 28), np.uint8)
    panel[58:] = cv2.resize(frame_rgb[:, :, ::-1], size, interpolation=cv2.INTER_AREA)
    index = int(np.searchsorted(arrays['source_frame_indices'], source_frame))
    available = index < len(arrays['source_frame_indices']) and arrays['source_frame_indices'][index] == source_frame
    text = label[:64] + ('' if available else ' | frame unavailable')
    cv2.putText(panel, text, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, .5, (210, 240, 225), 1, cv2.LINE_AA)
    cv2.putText(panel, f'Source frame {source_frame} | L:cyan R:gold | baseline:white',
                (10, 45), cv2.FONT_HERSHEY_SIMPLEX, .35, (180, 185, 185), 1, cv2.LINE_AA)
    if not available:
        return panel
    K = arrays['K'][index]
    sx, sy = width / frame_rgb.shape[1], height / frame_rgb.shape[0]
    for slot in (0, 1):
        if not arrays['geometry_valid'][index, slot]:
            continue
        if not include_absent and 'presence' in arrays and not arrays['presence'][index, slot]:
            continue
        points = arrays['joints_camera_m'][index, slot]
        pixels = points @ K.T
        good = (points[:, 2] > 1e-6) & np.isfinite(pixels).all(axis=1)
        pixels = pixels[:, :2] / np.maximum(pixels[:, 2:3], 1e-6)
        pixels *= (sx, sy); pixels[:, 1] += 58
        good &= np.max(np.abs(pixels), axis=1) < 100000
        pixels = np.rint(np.nan_to_num(pixels, nan=-10000)).clip(-100000, 100000).astype(int)
        color = (240, 213, 106) if slot == 0 else (128, 223, 255)
        if 'filled' in arrays and arrays['filled'][index, slot]:
            color = (255, 170, 90)
        mesh_drawn = False
        if geometry != 'skeleton' and 'vertices_camera_m' in arrays:
            from mint.visualization.draw import get_faces
            faces = get_faces()[1 if slot == 0 else 0]
            vertices = np.asarray(arrays['vertices_camera_m'][index, slot])
            projection = vertices @ K.T
            positive = (vertices[:, 2] > 1e-6) & np.isfinite(projection).all(axis=1)
            xy = projection[:, :2] / np.maximum(projection[:, 2:3], 1e-6)
            xy *= (sx, sy);xy[:, 1] += 58
            valid = positive[faces].all(axis=1) & (np.abs(xy[faces]).max(axis=(1, 2)) < 100000)
            faces = faces[valid]
            faces = faces[np.argsort(vertices[faces, 2].mean(axis=1))[::-1]]
            layer = panel.copy()
            for face in faces:
                cv2.fillConvexPoly(layer, np.rint(xy[face]).astype(np.int32), color, lineType=cv2.LINE_AA)
            cv2.addWeighted(layer, .5, panel, .5, 0, panel)
            mesh_drawn = len(faces) > 0
        if geometry == 'mesh' and mesh_drawn:
            continue
        for a, b in CONNECTIONS:
            if good[a] and good[b]:
                cv2.line(panel, tuple(pixels[a]), tuple(pixels[b]), color, 2, cv2.LINE_AA)
        for position in pixels[good]:
            cv2.circle(panel, tuple(position), 2, color, -1, cv2.LINE_AA)
    if baseline is not None:
        bindex = int(np.searchsorted(baseline['source_frame_indices'], source_frame))
        if bindex < len(baseline['source_frame_indices']) and baseline['source_frame_indices'][bindex] == source_frame:
            for slot in (0, 1):
                if not baseline['geometry_valid'][bindex, slot] or not include_absent and 'presence' in baseline and not baseline['presence'][bindex, slot]:
                    continue
                points = baseline['joints_camera_m'][bindex, slot]
                projected = points @ baseline['K'][bindex].T
                valid = (points[:, 2] > 1e-6) & np.isfinite(projected).all(axis=1)
                pixels = projected[:, :2] / np.maximum(projected[:, 2:3], 1e-6)
                pixels *= (sx, sy);pixels[:, 1] += 58
                for a, b in CONNECTIONS:
                    if not valid[a] or not valid[b] or np.abs(pixels[[a, b]]).max() > 100000:
                        continue
                    first, last = pixels[a], pixels[b]
                    length = float(np.linalg.norm(last - first))
                    for distance in np.arange(0, length, 10):
                        p = first + (last - first) * distance / max(length, 1)
                        q = first + (last - first) * min(distance + 5, length) / max(length, 1)
                        cv2.line(panel, tuple(np.rint(p).astype(int)), tuple(np.rint(q).astype(int)), (235, 240, 235), 1, cv2.LINE_AA)
    return panel


class ComparisonExports:
    def __init__(self, manager, *, autostart=True):
        self.manager = manager
        self.directory = manager.root / 'exports'
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._threads = {}
        self._stop = threading.Event()
        self._recover()
        if autostart:
            self._coordinator = threading.Thread(target=self._loop, name='comparison-exports', daemon=True)
            self._coordinator.start()
        atexit.register(self.close)

    def _read(self, export_id):
        path = self.directory / (export_id + '.json')
        return json.loads(path.read_text()) if path.is_file() else {'id': export_id, 'status': 'absent'}

    def _write(self, export_id, **values):
        with file_lock(self.directory / (export_id + '.state.lock')):
            state = self._read(export_id)
            atomic_json(self.directory / (export_id + '.json'), {**state, **values, 'updated_at': time.time()})

    @staticmethod
    def _alive(state):
        return bool(state.get('owner_pid') and state.get('owner_token') and
                    _token(state['owner_pid']) == state['owner_token'])

    def _recover(self):
        for path in self.directory.glob('*.json'):
            if len(path.stem) != 64:
                continue
            with file_lock(self.directory / (path.stem + '.state.lock')):
                try:
                    state = self._read(path.stem)
                except (OSError, ValueError):
                    continue
                if state.get('status') not in {'queued', 'rendering', 'running', 'cancelling'} or self._alive(state):
                    continue
                recoverable = (self.directory / (path.stem + '.request.json')).is_file()
                cancelled = (self.directory / (path.stem + '.cancel')).exists()
                if state['status'] == 'queued' and recoverable and not cancelled:
                    continue
                attempts = state.get('attempts', [])
                if attempts and not attempts[-1].get('finished_at'):
                    attempts[-1].update(finished_at=state.get('updated_at', time.time()), outcome='interrupted')
                recovered_files = list(state.get('recovered_partial_files', []))
                for temporary in self.directory.glob('.' + path.stem + '.*.mp4'):
                    # Dead-owner partial encodes are reversible cache artifacts.
                    # Make them visible to the ordinary bounded MP4 LRU budget.
                    destination = self.directory / ('interrupted-' + temporary.name[1:])
                    temporary.rename(destination)
                    recovered_files.append(str(destination))
                atomic_json(path, {**state, 'status': 'cancelled' if cancelled else 'queued' if recoverable else 'interrupted',
                    'attempts': attempts, 'recovered_partial_files': recovered_files,
                    'owner_pid': None, 'owner_token': None, 'updated_at': time.time(),
                    'recovery': 'Replay the durable request from frame zero; publish atomically' if recoverable else
                                'Legacy export has no durable request; submit the export again'})

    def _loop(self):
        while not self._stop.wait(.5) and not self.manager._stopping.is_set():
            self._recover()
            queued, selected = [], None
            try:
                # One encoder across all web processes sharing this cache.
                with file_lock(self.directory / '.execution.lock', blocking=False):
                    for path in self.directory.glob('*.json'):
                        if len(path.stem) == 64:
                            try:
                                state = self._read(path.stem)
                                if state.get('id') != path.stem:
                                    continue
                                if state.get('status') == 'queued' and not self._alive(state):
                                    queued.append(state)
                            except (OSError, ValueError):
                                continue
                    if queued:
                        selected = min(queued, key=lambda s: s.get('created_at', 0))
                        self._execute(selected['id'])
            except BlockingIOError:
                pass
            except Exception as error:
                if selected:
                    self._write(selected['id'], status='failed', error=str(error), owner_pid=None, owner_token=None)

    def _execute(self, export_id):
        current = self.status(export_id)  # Validate the path-bearing ID before opening a request.
        if current.get('source_invalidated'):
            self._write(export_id, status='failed', error=current['error'], owner_pid=None, owner_token=None)
            return
        with file_lock(self.directory / (export_id + '.state.lock')):
            state = self._read(export_id)
            if state['status'] != 'queued' or self._alive(state):
                return
            request_path = self.directory / (export_id + '.request.json')
            request = json.loads(request_path.read_text())
            if digest(request['identity']) != export_id:
                raise ValueError('Export request identity changed')
            identity, render = request['identity'], request['render']
            if (render['sample']['id'] != identity['sample'] or [r['id'] for r in render['runs']] != identity['runs'] or
                any(render.get(key) != identity.get(key) for key in ('include_absent', 'geometry', 'baseline_run')) or
                Path(render['sample']['frames_path']).resolve() != (self.manager.root / 'inputs' / identity['sample'] / 'frames.npy').resolve()):
                raise ValueError('Export render request differs from its frozen identity')
            attempts = list(state.get('attempts', []))
            attempts.append({'number': len(attempts) + 1, 'started_at': time.time(), 'owner_pid': os.getpid()})
            atomic_json(self.directory / (export_id + '.json'), {**state, 'status': 'rendering', 'error': None,
                'attempts': attempts, 'owner_pid': os.getpid(), 'owner_token': _token(os.getpid()), 'updated_at': time.time()})
        self._render(export_id, **request['render'])

    def cancel(self, export_id):
        state = self.status(export_id)
        if state['status'] in {'queued', 'rendering', 'running', 'cancelling'}:
            (self.directory / (export_id + '.cancel')).touch()
            self._write(export_id, status='cancelling' if self._alive(state) else 'cancelled')
        return self.status(export_id)

    def resume(self, export_id):
        state = self.status(export_id)
        if state.get('source_invalidated'):
            raise ValueError(state['error'])
        if state['status'] == 'absent':
            raise KeyError('Export not found')
        if state['status'] == 'completed' or self._alive(state):
            return state
        if not (self.directory / (export_id + '.request.json')).is_file():
            raise ValueError('Legacy export has no replayable request; submit the comparison export again')
        (self.directory / (export_id + '.cancel')).unlink(missing_ok=True)
        self._write(export_id, status='queued', error=None, owner_pid=None, owner_token=None)
        return self.status(export_id)

    def close(self):
        self._stop.set()
        thread = getattr(self, '_coordinator', None)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)

    def start(self, comparison_id, *, include_absent=False, run_ids=None, geometry='skeleton', baseline_run=None):
        if not isinstance(include_absent, bool):
            raise ValueError('include_absent must be a boolean display option')
        comparison = self.manager.comparison(comparison_id)
        runs = [run for run in comparison['runs'] if run['result']]
        if geometry not in {'skeleton', 'mesh', 'mesh_skel'}:
            raise ValueError('Unknown export geometry style')
        if run_ids is not None:
            if not isinstance(run_ids, list) or not run_ids or len(run_ids) != len(set(run_ids)) or any(value not in [run['id'] for run in runs] for value in run_ids):
                raise ValueError('Select completed member results for export')
            runs = sorted((run for run in runs if run['id'] in run_ids), key=lambda run: run_ids.index(run['id']))
        if baseline_run and baseline_run not in [run['id'] for run in comparison['runs'] if run['result']]:
            raise ValueError('Export baseline must be a completed result in this comparison')
        if not runs or not comparison['sample']:
            raise ValueError('Complete at least one result before exporting')
        identity = {'schema': 2, 'sample': comparison['sample_id'], 'runs': [r['id'] for r in runs], 'include_absent': include_absent,
                    'geometry': geometry, 'baseline_run': baseline_run,
                    'baseline_identity': file_identity(self.manager.root / 'runs' / baseline_run / 'result/result.json') if baseline_run else None,
                    'results': [file_identity(self.manager.root / 'runs' / r['id'] / 'result/result.json') for r in runs]}
        export_id = digest(identity)
        with self._lock, file_lock(self.directory / (export_id + '.state.lock')):
            state = self.status(export_id)
            if state['status'] == 'completed' or self._alive(state) or state['status'] == 'queued':
                return state
            atomic_json(self.directory / (export_id + '.request.json'), {'identity': identity,
                'comparison_id': comparison_id, 'render': {'sample': comparison['sample'], 'runs': runs,
                    'include_absent': include_absent, 'geometry': geometry, 'baseline_run': baseline_run}})
            (self.directory / (export_id + '.cancel')).unlink(missing_ok=True)
            atomic_json(self.directory / (export_id + '.json'), {**state, 'id': export_id, 'status': 'queued',
                'comparison_id': comparison_id, 'created_at': state.get('created_at', time.time()),
                'updated_at': time.time(), 'error': None, 'owner_pid': None, 'owner_token': None,
                'recovery_policy': 'automatic_replay', 'attempts': state.get('attempts', [])})
        return self.status(export_id)

    def status(self, export_id):
        if len(export_id) != 64 or any(c not in '0123456789abcdef' for c in export_id):
            raise KeyError('Invalid export ID')
        path = self.directory / (export_id + '.json')
        state = self._read(export_id)
        if state['status'] != 'absent':
            try:
                self._assert_sources(export_id, state)
            except (OSError, ValueError, KeyError) as error:
                return {**state, 'status': 'failed', 'source_invalidated': True,
                        'url': None, 'error': 'Export source is unavailable or withdrawn: ' + str(error)}
        if state['status'] == 'completed':
            if not (self.directory / (export_id + '.mp4')).is_file():
                state['status'] = 'evicted'
            else:
                state['url'] = '/api/comparison/exports/' + export_id + '/download'
        return state

    def _assert_sources(self, export_id, state=None):
        """A cached video cannot outlive a source annotation withdrawal."""
        request_path = self.directory / (export_id + '.request.json')
        if request_path.is_file():
            request = json.loads(request_path.read_text())
            identity = request['identity']
            keys = list(identity['runs']) + ([identity['baseline_run']] if identity.get('baseline_run') else [])
            sample_id = identity['sample']
        else:
            # Legacy videos without a durable request keep their recorded run
            # list when one is available; do not infer it from today's members.
            state = state or self._read(export_id)
            keys = list(state.get('run_ids', [])) + ([state['baseline_run']] if state.get('baseline_run') else [])
            sample_id = None
        for key in dict.fromkeys(keys):
            run = self.manager.run(key, summary=True)
            if not run['result'] or sample_id is not None and run['sample_id'] != sample_id:
                raise ValueError(run.get('error') or 'Published result is no longer available')

    def _render(self, export_id, sample, runs, include_absent, geometry='skeleton', baseline_run=None):
        from mint.visualization.encoding import atomic_video_file
        from mint.visualization.draw import H264PipeWriter
        path = self.directory / (export_id + '.mp4')
        state_path = path.with_suffix('.json')
        started = time.perf_counter()
        def progress(**values):
            self._write(export_id, **values)
        try:
            with file_lock(path.with_suffix('.lock')):
                if self.status(export_id)['status'] == 'completed':
                    return
                self._assert_sources(export_id)
                request = json.loads((self.directory / (export_id + '.request.json')).read_text())
                identity = request['identity']
                current = [file_identity(self.manager.root / 'runs' / r['id'] / 'result/result.json') for r in runs]
                if current != identity['results'] or (baseline_run and
                    file_identity(self.manager.root / 'runs' / baseline_run / 'result/result.json') != identity['baseline_identity']):
                    raise ValueError('Published result changed since this export was requested')
                from .prepare import prepare_sample
                sample = prepare_sample(sample['request'], Path(sample['frames_path']).parent,
                    cancel=lambda: self._cancelled(export_id))
                columns = min(3, len(runs)); rows = math.ceil(len(runs) / columns)
                width = min(640, sample['width']); width += width % 2
                height = round(sample['height'] * width / sample['width']); height += height % 2
                size = (width * columns, (height + 58) * rows)
                budget = CacheBudget(self.directory, pattern='*.mp4', preserve_metadata=True, max_gib=float(os.environ.get('MINT_COMPARISON_EXPORT_MAX_GIB', '16')))
                expected = max(2**20, int(size[0] * size[1] * sample['frames'] * .25 / 8))
                with file_lock(Path(sample['frames_path']).parent / '.active.pin', shared=True), budget.reserve(path, expected):
                    if file_identity(sample['frames_path']) != sample['frames_identity']:
                        raise ValueError('Prepared input changed before export')
                    frames = np.load(sample['frames_path'], mmap_mode='r', allow_pickle=False)
                    results = [load_result(self.manager.root / 'runs' / r['id'] / 'result')[1] for r in runs]
                    baseline = load_result(self.manager.root / 'runs' / baseline_run / 'result')[1] if baseline_run else None
                    def render(destination):
                        writer = H264PipeWriter(destination, sample['fps'], size, preset='veryfast')
                        try:
                            for i, frame in enumerate(frames):
                                if self._cancelled(export_id):
                                    raise InterruptedError('Export interrupted at a frame boundary')
                                canvas = np.zeros((size[1], size[0], 3), np.uint8)
                                for j, (run, arrays) in enumerate(zip(runs, results)):
                                    panel = overlay_frame(frame, arrays, sample['source_frame_indices'][i], (width, height), run['label'], include_absent=include_absent,
                                        geometry=geometry, baseline=baseline if run['id'] != baseline_run else None)
                                    y, x = (j // columns) * (height + 58), (j % columns) * width
                                    canvas[y:y + height + 58, x:x + width] = panel
                                writer.write(canvas)
                                if i % 48 == 0:
                                    self._assert_sources(export_id)
                                    progress(status='rendering', done=i + 1, total=sample['frames'])
                                    if shutil.disk_usage(self.directory).free < budget.minimum_free or destination.exists() and destination.stat().st_size > budget.maximum:
                                        raise OSError('Comparison export reached disk-space reserve or configured quota')
                        finally:
                            writer.close()
                    try:
                        atomic_video_file(path, render, sample['frames'])
                    finally:
                        frames._mmap.close()
                self._assert_sources(export_id)
                progress(status='completed', done=sample['frames'], total=sample['frames'],
                    seconds=time.perf_counter() - started, format='synchronized_2d_overlay_mp4',
                    include_absent=include_absent,
                    geometry=geometry, baseline_run=baseline_run, run_ids=[run['id'] for run in runs],
                    note='All sample frames; camera K from each run; independent 3D snapshot and numerical exports are available')
        except BaseException as exc:
            cancelled = (self.directory / (export_id + '.cancel')).exists()
            stopping = self._stop.is_set() or self.manager._stopping.is_set()
            progress(status='cancelled' if cancelled else 'queued' if stopping else 'failed', error=str(exc))
        finally:
            state = self._read(export_id)
            attempts = state.get('attempts', [])
            if attempts:
                attempts[-1].update(finished_at=time.time(), outcome=state['status'])
            progress(owner_pid=None, owner_token=None, attempts=attempts)

    def _cancelled(self, export_id):
        return self._stop.is_set() or self.manager._stopping.is_set() or (self.directory / (export_id + '.cancel')).exists()
