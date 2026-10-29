"""Per-job stage timings; GPU synchronization makes stage attribution explicit."""
import logging
import time
from collections import defaultdict
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class StageProfiler:
    def __init__(self, torch=None, *, enabled=True, interval=30):
        self.enabled, self.interval = enabled, interval
        cuda = getattr(torch, 'cuda', None)
        self.cuda = cuda if enabled and cuda is not None and cuda.is_available() else None
        self.seconds = defaultdict(float)
        self.started = self.last_report = time.perf_counter()
        self.frames_started = None

    @contextmanager
    def measure(self, stage, *, gpu=False):
        if not self.enabled:
            yield
            return
        if gpu and self.cuda:
            self.cuda.synchronize()
        start = time.perf_counter()
        try:
            yield
        finally:
            if gpu and self.cuda:
                self.cuda.synchronize()
            self.seconds[stage] += time.perf_counter() - start

    def start_frames(self):
        self.frames_started = time.perf_counter()

    def report(self, completed, total, *, force=False):
        if not self.enabled:
            return
        now = time.perf_counter()
        if not force and now - self.last_report < self.interval:
            return
        self.last_report = now
        elapsed = now - (self.frames_started if self.frames_started is not None else self.started)
        fps = completed / elapsed if elapsed > 0 else 0.
        timings = ' '.join(f'{name}={seconds:.3f}s' for name, seconds in sorted(self.seconds.items()))
        allocated = f' gpu_allocated={self.cuda.memory_allocated() / 1024**2:.0f}MiB' if self.cuda else ''
        logger.info('MuseTalk timing: frames=%s/%s processing_fps=%.3f processing_elapsed=%.1fs '
                    'gpu_synchronized=%s %s%s', completed, total, fps, elapsed, bool(self.cuda), timings, allocated)
