"""Regression tests for model versions, shared raw windows and streaming media."""
import json
import os
import signal
import subprocess
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
from mint.inference.base import InferenceCancelled
from mint.inference.checkpoints import WindowCheckpoint
from mint.inference.frame_store import read_frames
from mint.runtime import atomic_json, digest, model_identity
from mint.visualization.artifacts import VideoArtifacts
from mint.visualization.encoding import validate_video
from visualization.viewer.store import Store
from visualization.viewer.routes import create_app


@pytest.fixture
def media_store(tmp_path, monkeypatch):
    monkeypatch.setenv('MINT_RENDER_CACHE_DIR', str(tmp_path / 'artifacts'))
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    weight = tmp_path / 'model.safetensors'
    weight.write_bytes(b'weights A')
    config = tmp_path / 'config.yaml'
    config.write_text((ROOT / 'configs/training/mint_step2.yaml').read_text())
    def make():
        return Store(None, root=tmp_path, default_mode='mesh_skel', max_frames=None,
            scene='test', cache_dir=tmp_path / ('render-' + str(time.time_ns())),
            config_path=str(config), ckpt_path=str(weight))
    store = make()
    store.make_same_store = make
    return store


@pytest.mark.parametrize('replacement', ['weights', 'configuration'])
def test_same_path_model_replacement_retires_loaded_predictor(media_store, replacement):
    store = media_store
    previous = SimpleNamespace(_cache_model_identity=model_identity(store.ckpt_path, store.config_path),
                               close=mock.Mock())
    store.predictor = previous
    store._predictor_ready.set()
    if replacement == 'weights':
        Path(store.ckpt_path).write_bytes(b'new weights at exactly the same path')
    else:
        with Path(store.config_path).open('a') as stream:
            stream.write('\nregression_version: 2\n')
    expected = model_identity(store.ckpt_path, store.config_path)
    generation = store._load_gen
    assert store.select_model(store.ckpt_path, expected) == expected
    store._wait_for_retirements()
    assert store._load_gen == generation + 1
    assert not store.predictor_ready() and store.predictor is None
    previous.close.assert_called_once()


def test_file_replaced_during_model_loading_is_not_published(media_store):
    store = media_store
    entered, release = threading.Event(), threading.Event()
    original = model_identity(store.ckpt_path, store.config_path)
    loaded = SimpleNamespace(_cache_model_identity=original, close=mock.Mock(), parallel_device_names=['cpu'])
    def factory(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return loaded
    with mock.patch('inference.registry.get_predictor', side_effect=factory):
        store.start_load()
        assert entered.wait(3)
        Path(store.ckpt_path).write_bytes(b'replaced during construction')
        release.set()
        deadline = time.monotonic() + 5
        while store.loader_active() and time.monotonic() < deadline:
            time.sleep(.01)
    assert not store.predictor_ready()
    assert isinstance(store._load_err, RuntimeError)
    loaded.close.assert_called_once()


def test_raw_window_lease_waits_without_overwriting_owner_progress(tmp_path):
    identity = {'model': 'A', 'input': 'video', 'hand_mode': 'smooth@0.7,0.5,0.3'}
    first = WindowCheckpoint(identity, [(0, 2)], directory=tmp_path)
    before = first.manifest.read_bytes()
    waiting, acquired = threading.Event(), threading.Event()
    result = {}
    def second():
        with_task = WindowCheckpoint({**identity, 'hand_mode': 'blend'}, [(0, 2)],
            directory=tmp_path, on_wait=waiting.set)
        try:
            acquired.set()
            result['value'] = with_task.load(0)
            with_task.finish('inference_complete')
            result['metadata'] = with_task.metadata
        finally:
            with_task.close()
    worker = threading.Thread(target=second)
    worker.start()
    try:
        assert waiting.wait(3) and not acquired.is_set()
        assert first.manifest.read_bytes() == before
        first.save(0, {'pose_enc': np.zeros((1, 2, 9), np.float32)})
        first.finish('inference_complete')
    finally:
        first.close()
    worker.join(5)
    assert not worker.is_alive() and result['value'] is not None
    assert result['metadata']['new_windows'] == 0
    assert result['metadata']['reused_windows'] == 1


def test_checkpoint_wait_can_be_cancelled_without_changing_owner(tmp_path):
    first = WindowCheckpoint({'model': 'A'}, [(0, 2)], directory=tmp_path)
    before = first.manifest.read_bytes()
    try:
        with pytest.raises(InferenceCancelled):
            WindowCheckpoint({'model': 'A'}, [(0, 2)], directory=tmp_path,
                             cancel_check=lambda: True, cancelled_error=InferenceCancelled)
        assert first.manifest.read_bytes() == before
    finally:
        first.close()


def test_crashed_checkpoint_owner_can_be_taken_over(tmp_path):
    marker = tmp_path / 'written'
    script = """import sys,time
from pathlib import Path
import numpy as np
from mint.inference.checkpoints import WindowCheckpoint
task=WindowCheckpoint({'model':'A'},[(0,2),(2,4)],directory=sys.argv[1])
task.save(0,{'pose_enc':np.zeros((1,2,9),np.float32)})
Path(sys.argv[2]).write_text('ready')
time.sleep(60)
"""
    owner = subprocess.Popen([sys.executable, '-c', script, str(tmp_path), str(marker)], cwd=ROOT)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists():
            assert owner.poll() is None and time.monotonic() < deadline
            time.sleep(.02)
        owner.kill(); owner.wait(5)
        task = WindowCheckpoint({'model': 'A'}, [(0, 2), (2, 4)], directory=tmp_path)
        try:
            assert task.load(0) is not None and task.load(1) is None
            task.save(1, {'pose_enc': np.zeros((1, 2, 9), np.float32)})
            task.finish('inference_complete')
            record = json.loads(task.manifest.read_text())
            assert record['completed_windows'] == 2 and record['new_windows'] == record['reused_windows'] == 1
        finally:
            task.close()
    finally:
        if owner.poll() is None:
            owner.kill(); owner.wait(5)


def test_legacy_windows_migrate_and_different_bounds_are_not_reused(tmp_path):
    identity = {'model': 'A', 'input': 'video', 'hand_mode': 'smooth', 'schema': 1, 'bounds': [(0, 2)]}
    folder = tmp_path / digest(identity)
    folder.mkdir()
    atomic_json(folder / 'task.json', {'identity': identity})
    np.savez_compressed(folder / '00000000.npz', pose_enc=np.zeros((1, 2, 9), np.float32))
    task = WindowCheckpoint({k:v for k,v in identity.items() if k not in {'schema', 'bounds'}},
                            [(0, 2)], directory=tmp_path)
    try:
        assert task.load(0) is not None and not (folder / '00000000.npz').exists()
        other = WindowCheckpoint({'model': 'A', 'input': 'video', 'hand_mode': 'hard'},
                                 [(0, 1), (1, 2)], directory=tmp_path)
        try:
            assert other.raw_id != task.raw_id and other.load(0) is None
        finally:
            other.close()
    finally:
        task.close()


def test_smoothing_tuning_reuses_network_and_matches_full_recompute(tmp_path, monkeypatch):
    import torch
    from tests.test_idle_engine import make_engine
    from mint.inference.engine import StudentEngine
    monkeypatch.setenv('MINT_WINDOW_CACHE_DIR', str(tmp_path))
    frames = torch.arange(8*3*2*2, dtype=torch.float32).reshape(8, 3, 2, 2) / 100
    def engine():
        value = make_engine(StudentEngine)
        value._cache_model_identity = 'same-model'
        return value
    first = engine().predict(frames, preprocessed=True, hand_mode='smooth@0.7,0.5,0.3',
                             cache_identity={'input': 'same'})
    for mode in ('smooth@0.8,0.5,0.3', 'blend'):
        resumed = engine()
        with mock.patch.object(resumed, '_forward_window_batch', side_effect=AssertionError('unexpected network call')):
            actual = resumed.predict(frames, preprocessed=True, hand_mode=mode, cache_identity={'input': 'same'})
        baseline = engine().predict(frames, preprocessed=True, hand_mode=mode)
        assert actual['_task']['raw_id'] == first['_task']['raw_id']
        assert actual['_task']['new_windows'] == 0
        for key in ('pose_enc', 'hand', 'hand_confidence'):
            np.testing.assert_array_equal(actual[key], baseline[key])


class Batch:
    def __init__(self, data): self.data = data
    def asnumpy(self): return self.data


class Reader:
    def __init__(self):
        self.data = np.arange(300*64*64*3, dtype=np.int64).astype(np.uint8).reshape(300, 64, 64, 3)
    def get_batch(self, indices): return Batch(self.data[indices])


def test_decode_and_prediction_owners_do_not_form_a_wait_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    monkeypatch.setenv('MINT_DECODE_PREFETCH_MIB', '1')
    source = tmp_path / 'source.mp4'; source.write_bytes(b'source')
    def decode():
        return read_frames(Reader(), np.arange(300), source_path=source, cache_dir=tmp_path / 'rgb',
                           memory_mib=.01, decode_mib=.1, pipeline=True)
    # B owns the RGB writer while A already owns the raw prediction task.
    b = decode(); b[:4]
    first = WindowCheckpoint({'model': 'A'}, [(0, 2)], directory=tmp_path / 'windows')
    a = decode()
    outcome = {}
    def waiter():
        task = WindowCheckpoint({'model': 'A'}, [(0, 2)], directory=tmp_path / 'windows', on_wait=b.allow_complete)
        try: outcome['acquired'] = True
        finally: task.close()
    waiting = threading.Thread(target=waiter, daemon=True)
    waiting.start()
    try:
        deadline = time.monotonic() + 5
        while not a.metadata['complete'] and time.monotonic() < deadline:
            time.sleep(.02)
        assert a.metadata['complete'] and b.metadata['complete']
        assert not outcome
    finally:
        first.close(); a.close(); b.close()
    waiting.join(5)
    assert not waiting.is_alive() and outcome['acquired']


def test_decode_pipeline_overlaps_has_backpressure_and_preserves_pixels(tmp_path, monkeypatch):
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    monkeypatch.setenv('MINT_DECODE_PREFETCH_MIB', '1')
    source = tmp_path / 'source.mp4'; source.write_bytes(b'source')
    reader = Reader()
    frames = read_frames(reader, np.arange(300), source_path=source, cache_dir=tmp_path / 'rgb',
                         memory_mib=.01, decode_mib=.1, pipeline=True)
    try:
        np.testing.assert_array_equal(frames[:4], reader.data[:4])
        assert frames.metadata['read_before_decode_complete']
        time.sleep(.1)
        assert frames.metadata['ready_frames'] < 300
        assert frames.metadata['peak_ahead_frames'] <= frames.metadata['prefetch_frames'] + 9
        np.testing.assert_array_equal(frames.wait_complete(), reader.data)
        assert frames.metadata['complete']
        assert not list((tmp_path / 'rgb').glob('*.partial-*'))
    finally:
        frames.close()


def test_decode_pipeline_cancel_removes_partial_and_reservations(tmp_path, monkeypatch):
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    monkeypatch.setenv('MINT_DECODE_PREFETCH_MIB', '1')
    source = tmp_path / 'source.mp4'; source.write_bytes(b'source')
    frames = read_frames(Reader(), np.arange(300), source_path=source, cache_dir=tmp_path / 'rgb',
                         memory_mib=.01, decode_mib=.1, pipeline=True)
    frames[:4]
    frames.close()
    assert not frames._thread.is_alive()
    assert not list((tmp_path / 'rgb').glob('*.npy'))
    assert json.loads((tmp_path / 'rgb/.reservations').read_text()) == {}


def test_pipeline_and_eager_engine_outputs_are_identical(tmp_path, monkeypatch):
    from tests.test_idle_engine import make_engine
    from mint.inference.engine import StudentEngine
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    monkeypatch.setenv('MINT_DECODE_PREFETCH_MIB', '1')
    source = tmp_path / 'source.mp4'; source.write_bytes(b'source')
    reader = Reader()
    frames = read_frames(reader, np.arange(300), source_path=source, cache_dir=tmp_path / 'rgb',
                         memory_mib=.01, decode_mib=.1, pipeline=True)
    try:
        actual = make_engine(StudentEngine).predict(frames, hand_mode='blend')
        reference = make_engine(StudentEngine).predict(reader.data, hand_mode='blend')
        for key in ('pose_enc', 'hand', 'hand_confidence'):
            np.testing.assert_array_equal(actual[key], reference[key])
        assert actual['_timings']['decode_pipeline']['read_before_decode_complete']
    finally:
        frames.close()


def write_video(path, value=0):
    from visualization.render.draw import H264PipeWriter
    writer = H264PipeWriter(path, 30., (8, 8))
    for _ in range(3): writer.write(np.full((8, 8, 3), value, np.uint8))
    writer.close()
    return path


def test_video_result_identity_survives_restart_and_is_input_specific(media_store):
    store = media_store
    source = store.root / 'first.mp4'; source.write_bytes(b'input one')
    other = store.root / 'other.mp4'; other.write_bytes(b'input two')
    store.predictor = SimpleNamespace(_cache_model_identity='loaded-A')
    eid = store.video_eid(str(source))
    frames = np.zeros((3, 8, 8, 3), np.uint8)
    store.raw = lambda *a, **k: {'frames': frames, 'episode_index': 0}
    store.pred = lambda *a, **k: {'hand': np.zeros((3, 218), np.float32)}
    store.pred_world = lambda *a, **k: {}
    from visualization.render import compare
    with mock.patch.object(compare, 'render_pred_overlay', side_effect=lambda frames,pred,path,**kw: write_video(path)) as render:
        artifact = store.mp4_pred(eid, 'mesh_skel', 'max_chunked', 'hard')
        assert int(validate_video(artifact, 3)['nb_frames']) == 3
        restarted = store.make_same_store()
        restarted.predictor = store.predictor
        eid2 = restarted.video_eid(str(source))
        restarted.raw = mock.Mock(side_effect=AssertionError('cached export must not decode'))
        assert restarted.mp4_pred(eid2, 'mesh_skel', 'max_chunked', 'hard') == artifact
        assert render.call_count == 1
        different = store.make_same_store()
        different.predictor = store.predictor
        assert different.media_context(different.video_eid(str(other)))['input_id'] != store.media_context(eid)['input_id']
        artifact.write_bytes(b'corrupt cached video')
        regenerated = store.mp4_pred(eid, 'mesh_skel', 'max_chunked', 'hard')
        assert regenerated == artifact and render.call_count == 2
        validate_video(regenerated, 3)
    client = create_app(store).test_client()
    with mock.patch.object(store, 'mp4', return_value=artifact):
        resolved = client.get(f'/api/media/resolve/video/{eid}?hand_mode=hard')
        assert resolved.status_code == 200 and resolved.json['result_id'] == artifact.stem
        stale = client.get(f'/api/media/resolve/video/{eid}?input_id=previous-input')
        assert stale.status_code == 409
        legacy = client.get(f'/video/{eid}.mp4?hand_mode=hard')
        assert 'no-store' in legacy.headers['Cache-Control']
        legacy.close()
    video = client.get('/' + resolved.json['url'], headers={'Range': 'bytes=0-127'})
    assert video.status_code == 206 and len(video.data) == 128
    assert 'max-age=3600' in video.headers['Cache-Control']
    video.close()


def test_artifact_identity_ignores_link_count_but_detects_changed_bytes(tmp_path, monkeypatch):
    from mint.visualization.encoding import link_video
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    source = write_video(tmp_path / 'source.mp4')
    artifacts = VideoArtifacts(tmp_path / 'index')
    result = artifacts.resolve({'input': 'A'}, lambda: source, expected_frames=3)
    link_video(result, tmp_path / 'user-output.mp4')
    assert artifacts.lookup(result.stem) == result
    result.write_bytes(b'broken')
    assert artifacts.lookup(result.stem) is None


def test_composed_export_survives_viewer_restart(media_store):
    source = write_video(media_store.root / 'source.mp4')
    result = media_store._compose_export([('both_2d_pred', source)], tile_size=(8, 8))
    restarted = media_store.make_same_store()
    with mock.patch.object(restarted, '_compose_export_uncached', side_effect=AssertionError('must reuse complete export')):
        assert restarted._compose_export([('both_2d_pred', source)], tile_size=(8, 8)) == result
    validate_video(result, 3)


@pytest.mark.skipif(not Path('/proc/self/stat').exists(), reason='Linux process session ownership')
def test_supervisor_reaps_grandchild_before_restarting_and_on_stop(tmp_path):
    launcher = tmp_path / 'launcher.py'
    pids = tmp_path / 'children.txt'
    launcher.write_text("import subprocess,sys,time\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nwith open(sys.argv[1],'a') as f:f.write(str(p.pid)+'\\n')\ntime.sleep(60)\n")
    state, log = tmp_path / 'state.json', tmp_path / 'log'
    supervisor = subprocess.Popen([sys.executable, '-m', 'mint.service', '--state', str(state),
        '--log', str(log), '--', sys.executable, str(launcher), str(pids)], cwd=ROOT, start_new_session=True)
    def wait_for(condition):
        deadline = time.monotonic() + 12
        while not condition():
            assert supervisor.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.05)
    def alive(pid):
        try: return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except FileNotFoundError: return False
    try:
        wait_for(lambda: state.exists() and pids.exists())
        original = json.loads(state.read_text())
        old_grandchild = int(pids.read_text().splitlines()[0])
        os.kill(original['child_pid'], signal.SIGKILL)
        wait_for(lambda: len(pids.read_text().splitlines()) == 2)
        assert not alive(old_grandchild)
        new_grandchild = int(pids.read_text().splitlines()[1])
        supervisor.send_signal(signal.SIGTERM)
        assert supervisor.wait(8) == 0 and not alive(new_grandchild)
    finally:
        if supervisor.poll() is None:
            supervisor.send_signal(signal.SIGTERM)
            supervisor.wait(10)
