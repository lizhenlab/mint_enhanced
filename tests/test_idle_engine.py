"""Compare real engine stitching with CPU test replicas and simulated GPU admission."""

import copy
import sys
import threading
import unittest
from unittest import mock
from pathlib import Path

import numpy as np
import torch

from mint.inference.engine import StudentEngine as CliEngine, _ensure_model_train_on_path
from mint.inference.gpu_scheduler import GpuSample, IdleGpuScheduler
from mint.inference.window_frames import WindowFrames

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'eval/model_effect'))
from inference.engine import StudentEngine as ViewerEngine

_ensure_model_train_on_path()


class Lease:
    def __init__(self, uuid):
        pass
    def acquire(self):
        return True
    def release(self):
        pass


class TinyModel(torch.nn.Module):
    enable_hand = enable_hand_presence = True

    def forward(self, batch):
        x = batch['images'][:, :, 0, 0, 0]
        pose = torch.zeros((*x.shape, 9), dtype=torch.float32)
        pose[:, :, 0] = x
        pose[:, :, 6] = 1
        pose[:, :, 7:] = 1
        return {'pose_enc': pose, 'hand': x[:, :, None].expand(-1, -1, 218),
                'hand_presence_logits': x[:, :, None].expand(-1, -1, 2)}


def make_engine(cls):
    engine = cls.__new__(cls)
    engine.model = TinyModel()
    engine.models = [engine.model]
    engine.device = torch.device('cpu')
    engine.devices = [engine.device]
    engine._model_locks = [threading.Lock()]
    engine._forward_pool = None
    engine.window = engine.full_max_frames = 4
    engine.size_hw = (2, 2)
    engine.compile_mode = engine.fp8_mode = None
    engine.compile_mode_requested = engine.fp8_mode_requested = None
    engine.has_hand = engine.has_hand_presence = True
    return engine


class EngineTests(unittest.TestCase):
    def test_raw_video_over_450_frames_matches_existing_preprocessing_and_stitching(self):
        from data.transforms import preprocess_frames
        raw = np.arange(600 * 8 * 12 * 3, dtype=np.int64).astype(np.uint8).reshape(600, 8, 12, 3)
        reference = preprocess_frames(raw, (2, 2))
        for cls in [CliEngine, ViewerEngine]:
            with self.subTest(engine=cls.__module__):
                expected = make_engine(cls).predict(reference, preprocessed=True, hand_mode='blend')
                actual = make_engine(cls).predict(raw, hand_mode='blend')
                self.assertEqual(len(actual['hand']), 600)
                for key in ['pose_enc', 'hand', 'hand_confidence']:
                    np.testing.assert_allclose(actual[key], expected[key], rtol=1e-6, atol=1e-6)
                self.assertEqual(actual['_timings']['preprocess_strategy'], 'per_window')
                self.assertLessEqual(actual['_timings']['preprocess_peak_native_frames'], 4)

    def test_native_conversion_chunk_respects_memory_budget(self):
        from data.transforms import preprocess_frames
        raw = np.arange(40 * 200 * 200 * 3, dtype=np.int64).astype(np.uint8).reshape(40, 200, 200, 3)
        with mock.patch.dict('os.environ', {'MINT_PREPROCESS_MEMORY_MIB': '1'}):
            source = WindowFrames(raw, (2, 2))
            result = source[:]
        np.testing.assert_allclose(result.numpy(), preprocess_frames(raw, (2, 2)).numpy(), rtol=1e-6, atol=1e-6)
        self.assertEqual(source.metadata['preprocess_peak_native_frames'], 1)

    def test_mano_cpu_override_avoids_default_cuda_device(self):
        from mint.visualization import mano as cli_mano
        from visualization.reproj_core import mano as viewer_mano
        for module in [cli_mano, viewer_mano]:
            with mock.patch.dict('os.environ', {'MINT_MANO_DEVICE': 'cpu'}), \
                 mock.patch.object(module, '_DEVICE', None), \
                 mock.patch('torch.cuda.is_available', side_effect=AssertionError('No CUDA probe needed')):
                self.assertEqual(module._get_device().type, 'cpu')

    def test_dynamic_window_dispatch_preserves_both_engines_outputs(self):
        for cls in [CliEngine, ViewerEngine]:
            for mode in ['chunked', 'max_chunked', 'full']:
                for hand in ['hard', 'blend']:
                    with self.subTest(engine=cls.__module__, mode=mode, hand=hand):
                        static = make_engine(cls)
                        dynamic = make_engine(cls)
                        dynamic._scheduling_context = threading.local()
                        def factory(device):
                            view = copy.copy(dynamic)
                            view._gpu_scheduler = None
                            view.models = [copy.deepcopy(dynamic.model)]
                            view.model = view.models[0]
                            view.devices = [torch.device('cpu')]
                            view._model_locks = [threading.Lock()]
                            return view
                        pool = IdleGpuScheduler(
                            {f'cuda:{i}': f'GPU-{i}' for i in range(8)}, factory,
                            lambda *_: None, probe=lambda: {
                                f'GPU-{i}': GpuSample(f'GPU-{i}', 40000, 40960, 0)
                                for i in range(8)}, poll_seconds=0.02,
                            lease_factory=Lease,
                        )
                        dynamic._gpu_scheduler = pool
                        count = 4 if mode == 'full' else 32
                        frames = torch.arange(count, dtype=torch.float32)[:, None, None, None].expand(-1, 3, 2, 2)
                        try:
                            expected = static.predict(frames, cam_mode=mode, hand_mode=hand, preprocessed=True)
                            actual = dynamic.predict(frames, cam_mode=mode, hand_mode=hand, preprocessed=True)
                            for key in ['pose_enc', 'hand', 'hand_presence_logits', 'hand_confidence']:
                                np.testing.assert_allclose(actual[key], expected[key], rtol=1e-6, atol=1e-6)
                            self.assertEqual(actual['_timings']['gpu_scheduler']['policy'], 'idle')
                        finally:
                            pool.close()


if __name__ == '__main__':
    unittest.main()
