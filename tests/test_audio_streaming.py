"""Global audio token alignment across Whisper and video block boundaries."""
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_audio_windows_cross_30_second_boundaries_without_reset_or_gpu_cache(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    np = pytest.importorskip('numpy')
    from lipsync_service.audio_stream import AudioFeatures, token_start
    samples = np.concatenate([np.full(30 * 16000, i * 1500, dtype='<f4') for i in range(3)] +
                             [np.full(5 * 16000, 4500, dtype='<f4')])
    def decode(args, **kwargs):
        Path(args[-1]).write_bytes(samples.tobytes())
    monkeypatch.setattr('lipsync_service.audio_stream.run_ffmpeg', decode)
    calls = []
    def encoder(feature, **kwargs):
        calls.append(float(feature.item()))
        hidden = (torch.arange(1500) + feature).reshape(1, 1500, 1)
        return SimpleNamespace(hidden_states=(hidden, hidden + 10000))
    rt = SimpleNamespace(torch=torch, device='cpu', dtype=torch.float32,
                         whisper=SimpleNamespace(encoder=encoder),
                         audio_processor=SimpleNamespace(feature_extractor=lambda x, **kw:
                             SimpleNamespace(input_features=torch.tensor(float(x[0])))))
    for fps in (Fraction(25), Fraction(30000, 1001)):
        stream = AudioFeatures(rt, Path('source.m4a'), tmp_path)
        try:
            # Cross several segment boundaries, then the final zero-padded tail.
            frames = [0, 1, *[int(boundary * fps) + delta for boundary in (30, 60, 90, 95)
                               for delta in (-1, 0, 1)]]
            for first in range(0, len(frames), 3):
                indices = frames[first:first + 3]
                actual = stream.features(indices, fps)
                expected = []
                for frame in indices:
                    values = [[float(token), float(token + 10000)] if 0 <= token < 4750 else [0., 0.]
                              for token in range(token_start(frame, fps), token_start(frame, fps) + 10)]
                    expected.append(torch.tensor(values).reshape(20, 1))
                assert torch.equal(actual, torch.stack(expected))
                assert len(stream.cache) <= 2
                assert all(value.device.type == 'cpu' for value in stream.cache.values())
        finally:
            stream.close()
        assert not (tmp_path / 'audio.f32').exists()
    assert set(calls) == {0., 1500., 3000., 4500.}
