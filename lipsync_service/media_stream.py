"""Bounded media subprocess I/O with inactivity deadlines and deterministic cleanup."""
import queue
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from fractions import Fraction

from .errors import InferenceError


def _finish(process):
    if process.poll() is None:
        process.kill()
    process.wait()


def _failure(stage, process, log):
    log.seek(0, 2)
    size = log.tell()
    log.seek(max(0, size - 3000))
    detail = log.read().decode('utf-8', errors='replace').strip()
    return InferenceError(f'{stage} failed (exit {process.returncode}): {detail}')


def lines(command, *, stage, idle_timeout=120, progress=None):
    """Yield stdout lines without buffering a whole probe/encode in RAM."""
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=log)
        messages = queue.Queue(maxsize=2)
        stopped = threading.Event()

        def pump():
            try:
                for line in iter(process.stdout.readline, b''):
                    while not stopped.is_set():
                        try:
                            messages.put(line, timeout=.1)
                            break
                        except queue.Full:
                            pass
                    if stopped.is_set():
                        return
            finally:
                while not stopped.is_set():
                    try:
                        messages.put(None, timeout=.1)
                        break
                    except queue.Full:
                        pass

        thread = threading.Thread(target=pump, name='media-stdout', daemon=True)
        thread.start()
        try:
            while True:
                try:
                    line = messages.get(timeout=idle_timeout)
                except queue.Empty:
                    raise InferenceError(f'{stage} stalled: no output for {idle_timeout}s') from None
                if line is None:
                    break
                if progress:
                    progress(stage)
                yield line.decode('utf-8', errors='replace').strip()
            try:
                process.wait(timeout=idle_timeout)
            except subprocess.TimeoutExpired:
                raise InferenceError(f'{stage} stalled while finishing after {idle_timeout}s') from None
            if process.returncode:
                raise _failure(stage, process, log)
        finally:
            stopped.set()
            _finish(process)
            thread.join(timeout=5)
            process.stdout.close()


def run_ffmpeg(args, *, stage, idle_timeout=120, progress=None):
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-nostats',
               '-progress', 'pipe:1', *map(str, args)]
    for _ in lines(command, stage=stage, idle_timeout=idle_timeout, progress=progress):
        pass


def frame_count(source_count, fps, audio_duration=None):
    count = source_count if audio_duration is None else int(Fraction(str(audio_duration)) * fps)
    if source_count < 1 or count < 1:
        raise ValueError('Video or audio is too short to generate one frame')
    if audio_duration is not None and abs(float(count / fps) - audio_duration) > .15:
        raise ValueError('Audio duration cannot be represented within 150ms at this video frame rate')
    return count


def frame_blocks(source_count, output_count, limit):
    """Output start and source indices; split exactly at ping-pong turnarounds."""
    if min(source_count, limit) < 1:
        raise ValueError('Frame count and block size must be positive')
    start = 0
    while start < output_count:
        position = start % (2 * source_count)
        reverse = position >= source_count
        remaining = (2 * source_count if reverse else source_count) - position
        count = min(limit, output_count - start, remaining)
        first = 2 * source_count - 1 - position if reverse else position
        yield start, [first - i if reverse else first + i for i in range(count)]
        start += count



@contextmanager
def video_writer(output, width, height, fps, *, idle_timeout=120, progress=None):
    """One encoder for the entire timeline; one pending full-resolution frame."""
    with tempfile.TemporaryFile() as log:
        command = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-f', 'rawvideo',
                   '-pixel_format', 'bgr24', '-video_size', f'{width}x{height}',
                   '-framerate', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264',
                   '-threads', '2', '-crf', '18', '-pix_fmt',
                   'yuv420p' if width % 2 == height % 2 == 0 else 'yuv444p', '-y', str(output)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        jobs, results = queue.Queue(maxsize=1), queue.Queue(maxsize=1)

        def pump():
            while True:
                data = jobs.get()
                if data is None:
                    return
                try:
                    process.stdin.write(data)
                    process.stdin.flush()
                    results.put(None)
                except (OSError, ValueError) as exc:
                    results.put(exc)
                    return

        thread = threading.Thread(target=pump, name='media-stdin', daemon=True)
        thread.start()

        def write(frame):
            if tuple(frame.shape) != (height, width, 3):
                raise ValueError('Video frame dimensions changed during inference')
            jobs.put(frame.tobytes())
            try:
                error = results.get(timeout=idle_timeout)
            except queue.Empty:
                raise InferenceError(f'Video encoding stalled: no frame accepted for {idle_timeout}s') from None
            if error:
                _finish(process)
                raise _failure('Video encoding', process, log) from error
            if progress:
                progress('Video encoding')

        completed = False
        try:
            yield write
            jobs.put(None)
            thread.join(timeout=5)
            process.stdin.close()
            try:
                process.wait(timeout=idle_timeout)
            except subprocess.TimeoutExpired:
                raise InferenceError(f'Video encoding stalled while finishing after {idle_timeout}s') from None
            if process.returncode:
                raise _failure('Video encoding', process, log)
            completed = True
        finally:
            _finish(process)
            if thread.is_alive():
                try:
                    jobs.put_nowait(None)
                except queue.Full:
                    pass
                thread.join(timeout=5)
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            if not completed:
                output.unlink(missing_ok=True)


def raw_frames(video, width, height, fps, start, count, *, idle_timeout=120, progress=None):
    """Bounded raw decoder; forward blocks share this generator and decoder GOPs."""
    offset = max(0, float(start / fps) - .00001)
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '2', '-ss', f'{offset:.9f}',
               '-i', str(video), '-map', '0:v:0', '-an', '-sn', '-dn', '-vsync', '0',
               '-frames:v', str(count), '-pix_fmt', 'bgr24', '-threads', '1', '-f', 'rawvideo', 'pipe:1']
    size = width * height * 3
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log)
        messages = queue.Queue(maxsize=1)
        stopped = threading.Event()

        def pump():
            try:
                for _ in range(count):
                    data = process.stdout.read(size)
                    while not stopped.is_set():
                        try:
                            messages.put(data, timeout=.1)
                            break
                        except queue.Full:
                            pass
                    if stopped.is_set() or len(data) != size:
                        return
            except (OSError, ValueError):
                pass  # Closing the generator kills the process and closes the pipe.

        thread = threading.Thread(target=pump, name='video-decoder', daemon=True)
        thread.start()
        try:
            for index in range(count):
                try:
                    data = messages.get(timeout=idle_timeout)
                except queue.Empty:
                    raise InferenceError(f'Video decode stalled at source frame {start + index}: '
                                         f'no frame for {idle_timeout}s') from None
                if len(data) != size:
                    process.wait(timeout=idle_timeout)
                    if process.returncode:
                        raise _failure('Video decode', process, log)
                    raise ValueError(f'Decoded video frame count mismatch at source frame {start + index}')
                if progress:
                    progress(f'Video decode at source frame {start + index}')
                yield data
            process.wait(timeout=idle_timeout)
            if process.returncode:
                raise _failure('Video decode', process, log)
        finally:
            stopped.set()
            _finish(process)
            thread.join(timeout=5)
            process.stdout.close()


class VideoFrames:
    """Reuse the forward decoder; seek only for bounded backwards blocks."""
    def __init__(self, video, width, height, fps, source_count, **options):
        self.video, self.width, self.height, self.fps = video, width, height, fps
        self.source_count, self.options = source_count, options
        self.stream, self.next_index = None, None

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def read_block(self, indices):
        import numpy as np
        first = min(indices)
        reverse = len(indices) > 1 and indices[0] > indices[-1]
        if reverse or first != self.next_index:
            self.close()
            count = len(indices) if reverse else self.source_count - first
            self.stream = raw_frames(self.video, self.width, self.height, self.fps, first, count, **self.options)
        frames = {}
        for index in range(first, first + len(indices)):
            try:
                data = next(self.stream)
            except StopIteration:
                raise ValueError(f'Decoded video frame count mismatch at source frame {index}') from None
            frame = np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3)
            frames[index] = frame
        self.next_index = first + len(indices)
        if reverse:
            self.close()
            self.next_index = None
        return [frames[index] for index in indices]
