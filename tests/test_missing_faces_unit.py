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


class Frame:
    shape = (120, 160, 3)

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
                    runtime.get_landmarks = lambda paths, shift: (
                        [runtime.no_face if i in missing else (10, 20, 100, 80) for i in range(6)],
                        [Frame() for _ in range(6)])
                    used_audio = []

                    class Features:
                        def __getitem__(self, indices):
                            used_audio.extend(indices)
                            return indices

                    runtime._audio_features = Mock(return_value=Features())
                    runtime.vae = SimpleNamespace(get_latents_for_unet=lambda crop: crop,
                                                  decode_latents=lambda batch: [Frame() for _ in batch])
                    runtime.pe = lambda features: features
                    runtime.unet = SimpleNamespace(model=Mock(side_effect=lambda batch, *a, **kw: SimpleNamespace(sample=batch)))
                    runtime.get_image = Mock(return_value=Frame())
                    selected = output_frame_indices(6, 2, duration)
                    expected_active = [i for i, source in enumerate(selected) if source not in missing]

                    def ffmpeg(args):
                        if args[0] == '-i':
                            for i in range(6):
                                (Path(args[-1]).parent / f'{i:08d}.png').write_bytes(f'original-{i}'.encode())
                        else:
                            generated = Path(args[args.index('-i') + 1]).parent
                            self.assertEqual(len(list(generated.iterdir())), len(selected))
                            for i, source in enumerate(selected):
                                expected = f'original-{source}'.encode() if source in missing else b'generated'
                                self.assertEqual((generated / f'{i:08d}.png').read_bytes(), expected)
                            Path(args[-1]).write_bytes(b'encoded')

                    def write(path, frame):
                        Path(path).write_bytes(b'generated')
                        return True

                    cv2 = SimpleNamespace(resize=lambda image, *a, **kw: image, INTER_LANCZOS4=1, imwrite=write)
                    with patch.dict(sys.modules, {'cv2': cv2, 'numpy': SimpleNamespace(uint8=None)}), \
                         patch('lipsync_service.musetalk_runtime.video_timing', return_value=(Fraction(2), 6)), \
                         patch('lipsync_service.musetalk_runtime.probe', return_value=duration), \
                         patch('lipsync_service.musetalk_runtime.ffmpeg', side_effect=ffmpeg):
                        runtime.run(root / 'v.mp4', root / 'a.wav', root / 'out.mp4', timeline=timeline)
                    self.assertEqual(used_audio, expected_active)
                    self.assertEqual(runtime.get_image.call_count, len(expected_active))
                    if not expected_active:
                        runtime._audio_features.assert_not_called()
                        runtime.unet.model.assert_not_called()
                    self.assertEqual(list(root.iterdir()), [root / 'out.mp4'])
