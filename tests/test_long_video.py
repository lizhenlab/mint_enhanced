"""Long-video memory behavior, cache safety and native timeline coverage."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from mint.inference.base import InferenceCancelled
from mint.inference.frame_store import read_frames, sample_indices


class ArrayBatch:
    def __init__(self, array):
        self.array = array
    def asnumpy(self):
        return self.array


class Reader:
    def __init__(self):
        self.calls = []
        self.data = np.arange(64 * 8 * 12 * 3, dtype=np.int64).astype(np.uint8).reshape(64, 8, 12, 3)
    def get_batch(self, indices):
        self.calls.append(list(indices))
        return ArrayBatch(self.data[indices])


class FrameStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.mp4'
        self.source.write_bytes(b'fake source identity')

    def load(self, reader, **kwargs):
        return read_frames(reader, np.arange(64), source_path=self.source,
                           cache_dir=self.root / 'cache', memory_mib=0.001,
                           decode_mib=0.001, **kwargs)

    def test_native_timeline_has_no_450_frame_limit(self):
        indices, fps = sample_indices(10428, 36)
        self.assertEqual(len(indices), 10428)
        self.assertEqual((indices[0], indices[-1], fps), (0, 10427, 36))

    def test_fractional_sampling_keeps_timing_instead_of_rounded_stride(self):
        indices, fps = sample_indices(3600, 36, 30)
        self.assertEqual((len(indices), fps), (3000, 30))
        self.assertLess(abs(len(indices) / fps - 3600 / 36), 1 / fps)
        self.assertGreater(len(set(np.diff(indices))), 1)

    def test_memmap_preserves_all_pixels_with_small_decode_blocks(self):
        reader = Reader()
        output = self.load(reader)
        self.assertIsInstance(output, np.memmap)
        np.testing.assert_array_equal(output, reader.data)
        self.assertLessEqual(max(map(len, reader.calls)), 3)
        output._mmap.close()

    def test_cache_hit_does_not_decode_the_video_again(self):
        first = self.load(Reader())
        first._mmap.close()
        reader = Reader()
        cached = self.load(reader)
        self.assertEqual(reader.calls, [[0]])
        cached._mmap.close()

    def test_views_have_separate_cache_identity_and_correct_pixels(self):
        reader = Reader()
        left = self.load(reader, view='left')
        right = self.load(reader, view='right')
        self.assertNotEqual(left.filename, right.filename)
        np.testing.assert_array_equal(left, reader.data[:, :, :6])
        np.testing.assert_array_equal(right, reader.data[:, :, 6:])
        left._mmap.close()
        right._mmap.close()

    def test_cancel_removes_incomplete_cache_and_never_publishes_it(self):
        cancelled = False
        def progress(done, total):
            nonlocal cancelled
            cancelled = done > 3
        with self.assertRaises(InferenceCancelled):
            self.load(Reader(), on_step=progress, cancel_check=lambda: cancelled)
        self.assertEqual(list((self.root / 'cache').glob('*.npy')), [])
        self.assertEqual(list((self.root / 'cache').glob('*.json')), [])

    def test_source_changes_invalidate_the_cache(self):
        first = self.load(Reader())
        path = first.filename
        first._mmap.close()
        self.source.write_bytes(b'a new source identity with different size')
        second = self.load(Reader())
        self.assertNotEqual(path, second.filename)
        second._mmap.close()

    def test_small_videos_still_use_memory(self):
        reader = Reader()
        output = read_frames(reader, np.arange(64), memory_mib=1, decode_mib=0.001)
        self.assertNotIsInstance(output, np.memmap)
        np.testing.assert_array_equal(output, reader.data)

    def test_disk_exhaustion_fails_before_decoding_the_whole_video(self):
        with mock.patch('shutil.disk_usage', return_value=type('Usage', (), {'free': 0})()):
            with self.assertRaisesRegex(OSError, 'disk space'):
                self.load(Reader())


if __name__ == '__main__':
    unittest.main()
