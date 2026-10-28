"""Missing faces preserve frames and audio indices, without GPU dependencies."""
import sys
import tempfile
import unittest
from contextlib import nullcontext
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lipsync_service.musetalk_runtime import MuseTalkRuntime, output_frame_indices
from lipsync_service.media_stream import frame_blocks


class Frame:
    shape = (120, 160, 3)

    def __init__(self, identity="generated"):
        self.identity = identity

    def __getitem__(self, key):
        return self

    def copy(self):
        return self

    def astype(self, dtype):
        return self


class Batch(list):
    def to(self, **kwargs):
        return self


class MissingFaceTests(unittest.TestCase):
    def test_missing_faces_preserve_timeline_and_feature_positions(self):
        for timeline, duration in [('strict', None), ('audio', 5)]:
            # Includes leading/trailing missing faces, mixed batches, an entirely
            # missing batch and the all-missing case.
            for missing in ({0, 2, 3, 5}, set(range(6)), set()):
                with self.subTest(timeline=timeline, missing=missing), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    runtime = MuseTalkRuntime.__new__(MuseTalkRuntime)
                    runtime.no_face = (0., 0., 0., 0.)
                    runtime.batch_size, runtime.dtype, runtime.timesteps, runtime.fp = 2, None, None, None
                    runtime.torch = SimpleNamespace(inference_mode=nullcontext, cat=lambda values: Batch(values))
                    used_audio = []

                    class Features:
                        def __getitem__(self, indices):
                            used_audio.extend(indices)
                            return indices

                    audio_features = SimpleNamespace(features=Mock(side_effect=lambda indices, fps: Features()[indices]))
                    runtime.vae = SimpleNamespace(get_latents_for_unet=lambda crop: crop,
                                                  decode_latents=lambda batch: [Frame() for _ in batch])
                    runtime.pe = lambda features: features
                    runtime.unet = SimpleNamespace(model=Mock(side_effect=lambda batch, *a, **kw: SimpleNamespace(sample=batch)))
                    runtime.get_image = Mock(return_value=Frame())
                    selected = output_frame_indices(6, 2, duration)
                    expected_active = [i for i, source in enumerate(selected) if source not in missing]

                    written = []
                    cv2 = SimpleNamespace(resize=lambda image, *a, **kw: image, INTER_LANCZOS4=1)
                    with patch.dict(sys.modules, {'cv2': cv2, 'numpy': SimpleNamespace(uint8=None)}):
                        for start, indices in frame_blocks(6, len(selected), 2):
                            runtime.get_landmarks = lambda paths, shift: (
                                [runtime.no_face if i in missing else (10, 20, 100, 80) for i in indices],
                                [Frame(i) for i in indices])
                            runtime._process_block(['frame'] * len(indices), indices, start, Fraction(2),
                                                   audio_features, lambda frame: written.append(frame.identity),
                                                   lambda stage: None)
                    self.assertEqual(used_audio, expected_active)
                    self.assertEqual(written, [source if source in missing else 'generated' for source in selected])
                    self.assertEqual(runtime.get_image.call_count, len(expected_active))
                    if not expected_active:
                        audio_features.features.assert_not_called()
                        runtime.unet.model.assert_not_called()
