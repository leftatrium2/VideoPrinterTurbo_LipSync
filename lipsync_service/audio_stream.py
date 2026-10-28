"""Whisper's original 30-second segments, loaded on demand with a bounded CPU cache."""
import math
from collections import OrderedDict
from fractions import Fraction

from .media_stream import run_ffmpeg


def token_start(frame_index, fps):
    return math.floor(Fraction(50 * frame_index, 1) / fps) - math.ceil(50 / fps) * 2


class AudioFeatures:
    def __init__(self, runtime, audio, directory, *, idle_timeout=120, progress=None):
        self.runtime, self.audio = runtime, audio
        self.path = directory / 'audio.f32'
        self.idle_timeout, self.progress = idle_timeout, progress
        self.cache = OrderedDict()
        self.source = None
        self.length = 0
        self.zero = None

    def close(self):
        self.cache.clear()
        if self.source is not None:
            self.source.close()
            self.source = None
        self.path.unlink(missing_ok=True)

    def _prepare(self):
        if self.source is not None:
            return
        run_ffmpeg(['-i', self.audio, '-vn', '-ac', '1', '-ar', '16000',
                    '-f', 'f32le', self.path], stage='Audio PCM decode',
                   idle_timeout=self.idle_timeout, progress=self.progress)
        self.length = self.path.stat().st_size // 4
        if self.length < 320:
            raise ValueError('Audio is too short for MuseTalk')
        self.source = self.path.open('rb')

    def _segment(self, index):
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        import numpy as np
        rt = self.runtime
        self.source.seek(index * 30 * 16000 * 4)
        samples = np.frombuffer(self.source.read(30 * 16000 * 4), dtype='<f4').copy()
        if not len(samples):
            raise ValueError(f'Missing audio segment {index}')
        mel = rt.audio_processor.feature_extractor(samples, return_tensors='pt', sampling_rate=16000).input_features
        hidden = rt.whisper.encoder(mel.to(device=rt.device, dtype=rt.dtype), output_hidden_states=True).hidden_states
        # Only the current encoder pass occupies VRAM; cached segments live on CPU.
        encoded = rt.torch.stack(hidden, dim=2).detach().cpu()[0]
        self.cache[index] = encoded
        while len(self.cache) > 2:
            self.cache.popitem(last=False)
        if self.progress:
            self.progress(f'Whisper audio segment {index}')
        return encoded

    def features(self, frame_indices, fps):
        self._prepare()
        rt = self.runtime
        valid_tokens = self.length // 320
        if self.zero is None:
            self.zero = rt.torch.zeros_like(self._segment(0)[0])
        windows = []
        for frame in frame_indices:
            tokens = []
            first = token_start(frame, fps)
            for token in range(first, first + 10):
                if 0 <= token < valid_tokens:
                    tokens.append(self._segment(token // 1500)[token % 1500])
                else:
                    tokens.append(self.zero)
            windows.append(rt.torch.stack(tokens))
        chunks = rt.torch.stack(windows)
        return chunks.reshape(len(frame_indices), -1, chunks.shape[-1]).to(device=rt.device, dtype=rt.dtype)
