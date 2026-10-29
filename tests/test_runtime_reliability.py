"""Fault, concurrency, recovery and quota regressions using CPU-only replicas."""
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval/model_effect'))
from mint.inference.base import InferenceCancelled, InputChanged
from mint.inference.cache_budget import CacheBudget, file_lock
from mint.inference.checkpoints import WindowCheckpoint, list_tasks
from mint.inference.gpu_scheduler import GpuSample, IdleGpuScheduler
from mint.runtime import file_identity, video_identity
from mint.visualization.encoding import validate_video
from visualization.viewer import netutil
from visualization.viewer.routes import create_app
from visualization.viewer.store import Store


class Predictor:
    _raw_mcfg = None
    supports_weight_reload = False
    parallel_device_names = ['cpu']
    def __init__(self, value=1, gate=None, entered=None):
        self.value, self.gate, self.entered = value, gate, entered
        self.calls = self.closed = 0
    def predict(self, frames, **kwargs):
        self.calls += 1
        if self.entered: self.entered.set()
        if self.gate: assert self.gate.wait(5)
        return {'pose_enc': np.full((len(frames), 9), self.value, np.float32)}
    def close(self): self.closed += 1


@pytest.fixture
def store(tmp_path):
    weight = tmp_path / 'model.safetensors'
    weight.write_bytes(b'test weights')
    return Store(None, root=tmp_path, default_mode='mesh_skel', max_frames=None,
                 scene='test', cache_dir=tmp_path / 'renders',
                 config_path=str(ROOT / 'configs/training/mint_step2.yaml'), ckpt_path=str(weight))


def test_occupied_port_never_signals_another_process():
    with mock.patch.object(netutil, 'port_free', return_value=False), \
         mock.patch.object(netutil, 'pids_on_port', return_value=[123456]), mock.patch('os.kill') as kill:
        with pytest.raises(OSError, match='occupied'):
            netutil.ensure_port(8011)
        kill.assert_not_called()


def test_model_switch_cancels_old_result_retires_and_cannot_pollute_cache(store):
    gate, entered = threading.Event(), threading.Event()
    first, second = Predictor(1, gate, entered), Predictor(2)
    store.predictor = first
    store._predictor_ready.set()
    eid = store.video_eid(str(store.root / 'fake.mp4'))
    store.raw = lambda *a, **k: {'frames': np.zeros((3, 4, 4, 3), np.uint8),
        'fps': 30, 'source_indices': np.arange(3)}
    errors = []
    def old_request():
        try: store.pred(eid, 'max_chunked', 'hard')
        except Exception as exc: errors.append(exc)
    thread = threading.Thread(target=old_request)
    thread.start()
    assert entered.wait(3)
    store.swap_ckpt(store.ckpt_path)
    assert first.closed == 0  # running windows must finish before resource retirement
    store.predictor = second
    store._predictor_ready.set()
    gate.set(); thread.join(5)
    assert not thread.is_alive()
    store._wait_for_retirements()
    assert first.closed == 1
    assert len(errors) == 1 and isinstance(errors[0], InferenceCancelled)
    assert store.pred(eid, 'max_chunked', 'hard')['pose_enc'][0, 0] == 2
    assert second.calls == 1


def test_replaced_video_gets_new_identity_and_old_api_is_rejected(store):
    source = store.root / 'replace.mp4'
    source.write_bytes(b'first source')
    eid = store.video_eid(str(source), 'left')
    def batch(n):
        return SimpleNamespace(frames_rgb=np.zeros((n, 4, 4, 3), np.uint8), fps=30,
            source_frames=n, frame_indices=np.arange(n), view='left')
    with mock.patch('mint.inference.video.read_video', side_effect=[batch(2), batch(3)]) as decoder:
        assert store.raw(eid)['T'] == 2
        source.write_bytes(b'replacement with a different size')
        with pytest.raises(InputChanged) as error:
            store.raw(eid)
        replacement = error.value.replacement_eid
        assert replacement != eid
        assert store.raw(replacement)['T'] == 3
        assert decoder.call_count == 2
        response = create_app(store).test_client().get(f'/api/world/{eid}')
        assert response.status_code == 409 and response.json['code'] == 'input_changed'


def test_batch_api_preserves_eye_and_rejects_invalid_view(tmp_path):
    captured = {}
    def start(**kwargs): captured.update(kwargs); return {'ok': True}
    client = create_app(SimpleNamespace(root=tmp_path, default_mode='mesh_skel', start_batch_inference=start)).test_client()
    response = client.post('/api/batch/start', json={'video_view': 'left', 'hand_mode': 'hard'})
    assert response.status_code == 200 and captured['video_view'] == 'left'
    response = client.post('/api/batch/start', json={'video_view': 'wrong'})
    assert response.status_code == 400


def test_failed_video_does_not_publish_partial_and_retry_renders(store):
    from visualization.render import compare, draw
    eid = store.video_eid(str(store.root / 'fake.mp4'))
    frames = np.zeros((3, 8, 8, 3), np.uint8)
    store.raw = lambda *a, **k: {'frames': frames, 'episode_index': 0}
    store.pred = lambda *a, **k: {'hand': np.zeros((3, 218), np.float32)}
    store.pred_world = lambda *a, **k: {}
    calls = []
    def render(frames, pred, path, **kwargs):
        calls.append(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if len(calls) == 1:
            Path(path).write_bytes(b'partial')
            raise RuntimeError('encoder fault')
        writer = draw.H264PipeWriter(path, 30., (8, 8))
        for frame in frames: writer.write(frame)
        writer.close()
    with mock.patch.object(compare, 'render_pred_overlay', side_effect=render):
        with pytest.raises(RuntimeError, match='encoder fault'):
            store.mp4_pred(eid, 'mesh_skel', 'max_chunked', 'hard')
        assert list(store.cache_dir.glob('*.mp4')) == []
        result = store.mp4_pred(eid, 'mesh_skel', 'max_chunked', 'hard')
        assert int(validate_video(result, 3)['nb_frames']) == 3
        assert store.mp4_pred(eid, 'mesh_skel', 'max_chunked', 'hard') == result
        assert len(calls) == 2


def test_cache_quota_never_evicts_a_live_pin(tmp_path):
    a, b = tmp_path / 'a.npy', tmp_path / 'b.npy'
    a.write_bytes(b'a' * 100)
    budget = CacheBudget(tmp_path, max_gib=150/2**30, min_free_gib=0)
    with file_lock(a.with_suffix('.pin'), shared=True):
        with pytest.raises(OSError, match='pinned'):
            with budget.reserve(b, 100): pass
        assert a.exists()
    with budget.reserve(b, 100): b.write_bytes(b'b' * 100)
    assert not a.exists() and b.exists()
    assert budget.status()['reserved_bytes'] == 0


def test_rolling_queue_keeps_fast_worker_busy_and_output_order():
    class Lease:
        def __init__(self, uuid): pass
        def acquire(self): return True
        def release(self): pass
    release, started = threading.Event(), threading.Event()
    counts = {'slow': 0, 'fast': 0}
    scheduler = IdleGpuScheduler({'slow': 'GPU-slow', 'fast': 'GPU-fast'}, lambda d: d, lambda *a: None,
        probe=lambda: {u: GpuSample(u, 40000, 40960, 0) for u in ('GPU-slow', 'GPU-fast')},
        poll_seconds=.01, lease_factory=Lease)
    def run(resource, device, item):
        counts[device] += 1
        if device == 'slow':
            started.set(); assert release.wait(4)
        else:
            assert started.wait(3)
            if counts['fast'] >= 6: release.set()
        return item
    try:
        assert list(scheduler.iter_map(range(20), run, max_inflight=8)) == list(range(20))
        assert counts['fast'] >= 6
    finally:
        release.set(); scheduler.close()


def test_interrupted_inference_resumes_only_missing_windows(tmp_path, monkeypatch):
    from test_idle_engine import make_engine
    from mint.inference.engine import StudentEngine
    import torch
    monkeypatch.setenv('MINT_WINDOW_CACHE_DIR', str(tmp_path / 'windows'))
    frames = torch.arange(24*3*2*2, dtype=torch.float32).reshape(24, 3, 2, 2)
    cancelled = threading.Event()
    engine = make_engine(StudentEngine)
    engine._cache_model_identity = 'test-checkpoint'
    def progress(done, total):
        if done == 3: cancelled.set()
    with pytest.raises(InferenceCancelled):
        engine.predict(frames, preprocessed=True, hand_mode='blend', window_batch_size=1,
            cache_identity={'fixture': 'same-source'}, cancel_check=cancelled.is_set, on_step=progress)
    assert len(list((tmp_path / 'windows').glob('*/*.npz'))) == 3
    resumed = make_engine(StudentEngine)
    resumed._cache_model_identity = 'test-checkpoint'
    result = resumed.predict(frames, preprocessed=True, hand_mode='blend', window_batch_size=1,
                              cache_identity={'fixture': 'same-source'})
    baseline = make_engine(StudentEngine).predict(frames, preprocessed=True, hand_mode='blend')
    for key in ('pose_enc', 'hand', 'hand_confidence'):
        np.testing.assert_allclose(result[key], baseline[key], rtol=1e-6, atol=1e-6)
    assert result['_task']['reused_windows'] == 3
    assert list_tasks(tmp_path / 'windows')[0]['status'] == 'inference_complete'


def test_checkpoint_identity_and_corruption_are_not_reused(tmp_path):
    task = WindowCheckpoint({'model': 'A', 'input': 'v1'}, [(0, 2)], directory=tmp_path)
    try:
        values = {'pose_enc': np.zeros((1, 2, 9), np.float32)}
        task.save(0, values)
        assert task.load(0) is not None
        (task.folder / '00000000.npz').write_bytes(b'broken archive')
        assert task.load(0) is None
        other = WindowCheckpoint({'model': 'B', 'input': 'v1'}, [(0, 2)], directory=tmp_path)
        try: assert other.id != task.id and other.load(0) is None
        finally: other.close()
    finally: task.close()


def test_equivalent_smoothing_parameters_reuse_cli_windows_in_viewer(tmp_path, monkeypatch):
    from test_idle_engine import make_engine
    from mint.inference.engine import StudentEngine
    from mint.inference.options import InferenceOptions
    from visualization.viewer.routes import _normalize_hand_mode
    import torch
    monkeypatch.setenv('MINT_WINDOW_CACHE_DIR', str(tmp_path / 'windows'))
    frames = torch.zeros((6, 3, 2, 2))
    first, second = make_engine(StudentEngine), make_engine(StudentEngine)
    first._cache_model_identity = second._cache_model_identity = 'same-model'
    identity = {'fixture': 'same-video'}
    result = first.predict(frames, preprocessed=True, hand_mode='smooth', cache_identity=identity)
    assert InferenceOptions(hand_mode='smooth@0.600,0.6,2.0').hand_mode == 'smooth'
    assert _normalize_hand_mode('smooth@0.6,0.6,2') == 'smooth'
    with mock.patch.object(second, '_forward_window_batch', side_effect=AssertionError('must reuse every window')):
        resumed = second.predict(frames, preprocessed=True, hand_mode='smooth@0.6,0.6,2', cache_identity=identity)
    assert result['_task']['id'] == resumed['_task']['id']
    assert resumed['_task']['new_windows'] == 0
    assert resumed['_task']['reused_windows'] == resumed['_task']['total_windows']


def test_benchmark_policy_is_passed_to_engine():
    from benchmark.predictor import StudentPredictor
    with mock.patch('inference.engine.HandReprojPredictor', return_value=SimpleNamespace(window=16)) as factory:
        StudentPredictor('fake', resource_policy='shared')
        assert factory.call_args.kwargs['devices'] == 'shared'


def test_shared_engine_and_exceptions_are_single_implementations():
    from mint.inference.engine import StudentEngine
    from inference.engine import StudentEngine as CompatibilityEngine
    from inference.base import InferenceCancelled as CompatibilityCancelled
    assert CompatibilityEngine is StudentEngine
    assert CompatibilityCancelled is InferenceCancelled


def test_recovery_uses_current_equivalent_config_after_release_path_moves(store, tmp_path, monkeypatch):
    import yaml
    from mint.runtime import digest
    monkeypatch.setenv('MINT_WINDOW_CACHE_DIR', str(tmp_path / 'windows'))
    source = tmp_path / 'source.mp4'; source.write_bytes(b'fixture')
    params = {'kind': 'video', 'source': str(source), 'checkpoint': store.ckpt_path,
              'config': str(tmp_path / 'removed-release/config.yaml'), 'cam_mode': 'max_chunked', 'hand_mode': 'hard'}
    identity = {'input': {'source': file_identity(source)},
                'model': digest({'weights': file_identity(store.ckpt_path),
                                 'config': yaml.safe_load(Path(store.config_path).read_text())})}
    task = WindowCheckpoint(identity, [(0, 2)], params)
    task.close()
    with mock.patch.object(store, 'start_load'), mock.patch.object(store, 'predictor_ready', return_value=True):
        response = create_app(store).test_client().post(f'/api/tasks/{task.id}/prepare')
    assert response.status_code == 200
    assert response.json['parameters']['config'] == store.config_path


def test_reloading_static_engine_cannot_reuse_previous_model_windows(tmp_path, monkeypatch):
    from test_idle_engine import make_engine
    from mint.inference.engine import StudentEngine
    import torch
    monkeypatch.setenv('MINT_WINDOW_CACHE_DIR', str(tmp_path / 'windows'))
    engine = make_engine(StudentEngine)
    engine._cache_model_identity, engine._cache_configuration = 'old-model', {'fixture': 'config'}
    frames = torch.zeros((6, 3, 2, 2)); identity = {'fixture': 'source'}
    first = engine.predict(frames, preprocessed=True, hand_mode='hard', cache_identity=identity)
    weights = tmp_path / 'replacement.pt'; weights.write_bytes(b'new weights')
    with mock.patch('utils.weight_loader.load_state_dict_file', return_value={}):
        engine.reload(str(weights))
    second = engine.predict(frames, preprocessed=True, hand_mode='hard', cache_identity=identity)
    assert first['_task']['id'] != second['_task']['id']
    assert second['_task']['reused_windows'] == 0


def test_partial_batch_pair_is_recoverable_and_stale_pair_conflicts(tmp_path):
    from visualization.viewer.batch import existing_result
    from mint.runtime import atomic_npz
    item = {'output_npz': tmp_path/'prediction.npz', 'output_mp4': tmp_path/'prediction.mp4'}
    assert existing_result(item, 'A') == 'new'
    atomic_npz(item['output_npz'], batch_signature=np.asarray('A'), frame_count=np.asarray(3),
               pose_enc=np.zeros((3, 9)), hand=np.zeros((3, 218)), source_frame_indices=np.arange(3))
    assert existing_result(item, 'A') == 'recoverable'
    assert existing_result(item, 'B') == 'conflict'

def test_task_list_recovers_process_exit_as_interrupted(tmp_path):
    task = WindowCheckpoint({'model': 'A'}, [(0, 2)], directory=tmp_path)
    task.update(pid=99999999, pid_token='terminated-process')
    task.close()
    assert list_tasks(tmp_path)[0]['status'] == 'interrupted'
