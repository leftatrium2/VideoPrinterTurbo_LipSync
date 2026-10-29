"""The production decoder path never encodes or writes intermediate PNGs."""
from fractions import Fraction

import pytest

from lipsync_service.media_stream import VideoFrames


def test_memory_blocks_keep_frame_order_dimensions_and_forward_decoder_reuse(monkeypatch, tmp_path):
    np = pytest.importorskip('numpy')
    calls = []
    def raw(video, width, height, fps, start, count, **kwargs):
        calls.append((start, count))
        for i in range(start, start + count):
            yield bytes([i]) * (width * height * 3)
    monkeypatch.setattr('lipsync_service.media_stream.raw_frames', raw)
    decoder = VideoFrames(tmp_path / 'input.mp4', 8, 4, Fraction(25), 6)
    try:
        a = decoder.read_block([0, 1, 2])
        b = decoder.read_block([3, 4, 5])
        assert calls == [(0, 6)]
        assert all(frame.shape == (4, 8, 3) for frame in a + b)
        assert [int(frame[0, 0, 0]) for frame in a + b] == list(range(6))
        reverse = decoder.read_block([5, 4, 3])
        assert [int(frame[0, 0, 0]) for frame in reverse] == [5, 4, 3]
        assert calls[-1] == (3, 3)
        assert list(tmp_path.iterdir()) == []
        # Older blocks retain their own bytes after subsequent decoding.
        assert int(a[0][0, 0, 0]) == 0
    finally:
        decoder.close()
