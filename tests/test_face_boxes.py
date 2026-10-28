"""CPU-only regressions; runnable with unittest without model dependencies."""
import sys
import tempfile
import unittest
from contextlib import nullcontext
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lipsync_service.musetalk_runtime import MuseTalkRuntime, normalize_face_box


class FaceBoxTests(unittest.TestCase):
    def test_clipping_each_edge_and_margin(self):
        cases = [
            ((20, 20, 100, 80), (20, 20, 100, 90)),
            ((-15, 20, 100, 80), (0, 20, 100, 90)),
            ((20, -15, 100, 80), (20, 0, 100, 90)),
            ((20, 20, 735, 80), (20, 20, 720, 90)),
            ((20, 20, 100, 1290), (20, 20, 100, 1280)),
            ((0, 308, 244, 658), (0, 308, 244, 668)),
            ((10, 1200, 100, 1278), (10, 1200, 100, 1280)),
        ]
        for box, expected in cases:
            with self.subTest(box=box):
                self.assertEqual(normalize_face_box(box, 720, 1280, 42), expected)

    def test_invalid_boxes_include_diagnostics(self):
        for box in [(-20, 10, -1, 100), (721, 10, 740, 100),
                    (10, 1281, 100, 1300), (10, -20, 100, -1),
                    (100, 20, 10, 80), (10, 80, 100, 80),
                    (10, 81, 100, 80), (10, 20, 10, 80),
                    (float('nan'), 20, 100, 80), (10, 20, float('inf'), 80),
                    (10, 20, 100)]:
            with self.subTest(box=box):
                with self.assertRaisesRegex(ValueError, r'frame 42 .*box=.*image=720x1280'):
                    normalize_face_box(box, 720, 1280, 42)

    def test_runtime_rejects_bad_second_frame_before_audio_or_inference(self):
        # Stub only decoding and model dependencies; exercise run() validation.
        runtime = MuseTalkRuntime.__new__(MuseTalkRuntime)
        runtime.torch = SimpleNamespace(inference_mode=nullcontext)
        runtime.no_face = (0., 0., 0., 0.)
        frame = SimpleNamespace(shape=(1280, 720, 3))
        for box, message in [((721, 10, 740, 100), 'Invalid face bounding box')]:
            with self.subTest(box=box), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'output.mp4'
                runtime.get_landmarks = lambda paths, shift: ([(10, 20, 100, 80), box], [frame, frame])

                from unittest.mock import Mock
                audio_features = Mock()
                write = Mock()
                with patch.dict(sys.modules, {'cv2': SimpleNamespace(), 'numpy': SimpleNamespace()}):
                    with self.assertRaisesRegex(ValueError, message) as caught:
                        runtime._process_block(['frame0', 'frame1'], [0, 1], 0, Fraction(30),
                                               audio_features, write, lambda stage: None)
                    self.assertIn('frame 1 (0-based)', str(caught.exception))
                    self.assertIn(f'box={box}', str(caught.exception))
                    self.assertIn('image=720x1280', str(caught.exception))
                    audio_features.features.assert_not_called()
                    write.assert_not_called()
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])



if __name__ == '__main__':
    unittest.main()
