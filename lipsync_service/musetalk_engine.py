"""Persistent Python worker; CUDA state never enters the HTTP process."""
import logging
import math
import multiprocessing
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .engine import finalize_output, validate_inputs
from .errors import InferenceError, InferenceTimeout, inference_detail

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SHUTDOWN_GRACE_SECONDS = 10


def _worker(connection, settings):
    # Imports here are intentional: spawn must not import torch in the API process.
    if os.name == 'posix':
        os.setsid()
    os.environ['CUDA_VISIBLE_DEVICES'] = settings['gpu']
    os.environ['TORCH_HOME'] = str(ROOT / '.cache' / 'torch')
    os.environ['HF_HOME'] = str(ROOT / '.cache' / 'huggingface')
    os.environ['XDG_CACHE_HOME'] = str(ROOT / '.cache')
    os.environ['MPLCONFIGDIR'] = str(ROOT / '.cache' / 'matplotlib')
    # Every required file is verified before loading. Startup must not make
    # implicit network downloads through Transformers/Diffusers.
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    log_path = Path(settings['runtime']) / 'worker.log'
    with log_path.open('a', buffering=1) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        # Spawn does not reliably inherit the API process's logging setup.
        # Bind to stderr (fd 2), which is redirected to each task's inference.log.
        family_logger = logging.getLogger('lipsync_service')
        previous_level, previous_propagate = family_logger.level, family_logger.propagate
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        family_logger.addHandler(handler)
        family_logger.setLevel(logging.INFO)
        family_logger.propagate = False
        try:
            # On Windows the parent attaches a kill-on-close Job Object before
            # this gate opens, so model initialization cannot orphan children.
            if connection.recv() != ('initialize', None):
                raise RuntimeError('Invalid MuseTalk initialization handshake')
            from .musetalk_runtime import MuseTalkRuntime
            runtime = MuseTalkRuntime(settings)
            runtime.progress = lambda stage: connection.send(('progress', stage))
            connection.send(('ready', None))
            while True:
                request = connection.recv()
                if request is None:
                    break
                video, audio, output = map(Path, request[:3])
                timeline = request[3]
                with output.with_name('inference.log').open('a', buffering=1) as task_log:
                    os.dup2(task_log.fileno(), 1)
                    os.dup2(task_log.fileno(), 2)
                    try:
                        runtime.run(video, audio, output, timeline=timeline)
                        connection.send(('done', None))
                    except Exception as exc:
                        logger.exception('MuseTalk inference failed')
                        connection.send(('error', f'MuseTalk inference failed: {inference_detail(exc)}; '
                                         'full traceback: inference.log'))
                    finally:
                        os.dup2(log.fileno(), 1)
                        os.dup2(log.fileno(), 2)
        except EOFError:
            pass
        except Exception:
            logger.exception('MuseTalk worker initialization failed')
            try:
                connection.send(('error', 'MuseTalk initialization failed; inspect .runtime/musetalk/worker.log'))
            except (BrokenPipeError, EOFError, OSError):
                pass
        finally:
            connection.close()
            family_logger.removeHandler(handler)
            handler.close()
            family_logger.setLevel(previous_level)
            family_logger.propagate = previous_propagate


class MuseTalkEngine:
    def __init__(self, *, model_dir=None, gpu='0', batch_size=1, use_float16=True,
                 timeout=0, startup_timeout=600, runtime_dir=None, worker_target=None, timeline='strict',
                 idle_timeout=600, media_idle_timeout=120, frame_memory_mb=256,
                 detect_max_side=1280, profile=True):
        if batch_size < 1:
            raise ValueError('MuseTalk batch size must be positive')
        if timeline not in ('strict', 'audio'):
            raise ValueError('Timeline must be strict or audio')
        if type(detect_max_side) is not int or detect_max_side < 0:
            raise ValueError('Detection max side must be a nonnegative integer')
        if type(profile) is not bool:
            raise ValueError('Profile must be a boolean')
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError('Inference timeout must be finite and nonnegative')
        if any(not math.isfinite(value) or value <= 0 for value in
               (startup_timeout, idle_timeout, media_idle_timeout, frame_memory_mb)):
            raise ValueError('Startup/idle timeouts and frame memory budget must be finite and positive')
        self.timeout, self.startup_timeout = timeout, startup_timeout
        self.idle_timeout = idle_timeout
        self.on_progress = None
        self.settings = {
            'source': str(ROOT / 'third_party' / 'MuseTalk'),
            'models': str(Path(model_dir or ROOT / 'models').resolve()),
            'runtime': str(Path(runtime_dir or ROOT / '.runtime' / 'musetalk').resolve()),
            'gpu': str(gpu), 'batch_size': batch_size, 'float16': use_float16, 'timeline': timeline,
            'media_idle_timeout': media_idle_timeout, 'frame_memory_mb': frame_memory_mb,
            'detect_max_side': detect_max_side, 'profile': profile,
        }
        self._target = worker_target or _worker
        self._process = self._connection = None
        self._loaded = False
        self._lock = threading.RLock()
        self._group_id = None
        self._windows_job = None
        self._closing = threading.Event()
        self._watcher = None

    @property
    def ready(self):
        process = self._process
        try:
            return bool(self._loaded and process and process.is_alive())
        except ValueError:  # A concurrent recovery has just closed this process.
            return False

    def _notify_progress(self, event):
        if self.on_progress:
            try:
                self.on_progress(event)
            except Exception as exc:
                logger.warning('MuseTalk progress callback failed (%s)', type(exc).__name__)

    def _receive(self, timeout, *, idle_timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else math.inf
        idle_deadline = time.monotonic() + idle_timeout if idle_timeout is not None else math.inf
        stage = 'waiting for worker'
        while time.monotonic() < min(deadline, idle_deadline):
            if self._connection.poll(min(.1, max(0, deadline - time.monotonic()))):
                try:
                    status, detail = self._connection.recv()
                    if status == 'progress':
                        stage = str(detail)[:500]
                        self._notify_progress(detail)
                        if idle_timeout is not None:
                            idle_deadline = time.monotonic() + idle_timeout
                        continue
                    return status, detail
                except (EOFError, OSError) as exc:
                    raise RuntimeError('MuseTalk worker exited unexpectedly') from exc
            if not self._process.is_alive():
                raise RuntimeError('MuseTalk worker exited unexpectedly')
        reason = 'total inference time limit reached' if time.monotonic() >= deadline else 'no progress'
        raise InferenceTimeout(f'MuseTalk worker timed out ({reason}); last stage: {stage}')

    def start(self):
        with self._lock:
            if self._closing.is_set():
                raise RuntimeError('MuseTalk engine is closed')
            if self.ready:
                return
            self._stop()
            Path(self.settings['runtime']).mkdir(parents=True, exist_ok=True)
            context = multiprocessing.get_context('spawn')
            parent, child = context.Pipe()
            self._connection = parent
            self._process = context.Process(target=self._target, args=(child, self.settings),
                                            name='musetalk-v15')
            try:
                self._process.start()
                child.close()
                if os.name == 'nt':
                    from .windows_process import WindowsProcessJob
                    self._windows_job = WindowsProcessJob(self._process.pid)
                if self._target is _worker:
                    self._connection.send(('initialize', None))
                status, detail = self._receive(self.startup_timeout)
                if status != 'ready':
                    raise RuntimeError(detail)
                if os.name == 'posix' and os.getpgid(self._process.pid) == self._process.pid:
                    self._group_id = self._process.pid
                self._loaded = True
                if self._watcher is None:
                    self._watcher = threading.Thread(target=self._supervise, name='musetalk-supervisor', daemon=True)
                    self._watcher.start()
            except BaseException:
                child.close()
                self._stop()
                raise

    def _supervise(self):
        delay = 1
        while not self._closing.wait(delay):
            if not self._lock.acquire(blocking=False):
                continue
            try:
                if self._closing.is_set():
                    return
                if not self.ready:
                    logger.warning('MuseTalk worker is unavailable; reinitializing')
                    self.start()
                delay = 1
            except Exception:
                logger.exception('MuseTalk worker recovery failed; retrying in 30 seconds')
                delay = 30
            finally:
                self._lock.release()

    def _stop(self, *, graceful=False):
        was_loaded = self._loaded
        self._loaded = False
        process, self._process = self._process, None
        group_id, self._group_id = self._group_id, None
        if (graceful and was_loaded and process is not None and process.is_alive()
                and self._connection is not None):
            try:
                # Returning normally lets multiprocessing run semaphore
                # finalizers. SIGKILL/TerminateProcess skips those finalizers.
                self._connection.send(None)
                process.join(timeout=SHUTDOWN_GRACE_SECONDS)
            except (BrokenPipeError, EOFError, OSError):
                pass
            if process.is_alive():
                logger.warning('MuseTalk worker did not exit within %ss; terminating its process tree',
                               SHUTDOWN_GRACE_SECONDS)
        if self._windows_job is not None:
            try:
                self._windows_job.close()
            except BaseException:
                self._process, self._group_id = process, group_id
                raise
            self._windows_job = None
        if process is not None and process.pid is not None:
            # Also kill ffmpeg descendants; never kill the API's process group.
            try:
                if group_id is not None:
                    os.killpg(group_id, signal.SIGKILL)
                elif os.name == 'posix' and os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.is_alive():
                    process.kill()
            except ProcessLookupError:
                pass
            except PermissionError:
                # Darwin can report EPERM for a group whose leader has already
                # exited. Linux deployment must surface real signaling errors.
                if sys.platform != 'darwin' or process.is_alive():
                    self._process, self._group_id = process, group_id
                    raise
            process.join(timeout=10)
            if process.is_alive():
                self._process, self._group_id = process, group_id
                raise RuntimeError('Could not stop MuseTalk worker')
            process.close()
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def close(self):
        self._closing.set()
        with self._lock:
            self._stop(graceful=True)
        if self._watcher is not None:
            self._watcher.join(timeout=10)

    def run(self, video: Path, audio: Path, output: Path):
        return self.run_timeline(video, audio, output, timeline=self.settings['timeline'])

    def run_timeline(self, video: Path, audio: Path, output: Path, *, timeline='strict'):
        video, audio, output = video.resolve(), audio.resolve(), output.resolve()
        duration = validate_inputs(video, audio, timeline)
        raw = output.with_name('model-output.mp4')
        with self._lock:
            self.start()
            try:
                self._connection.send((*map(str, (video, audio, raw)), timeline))
                status, detail = self._receive(self.timeout or None, idle_timeout=self.idle_timeout)
                if status != 'done':
                    raise InferenceError(detail)
                self._notify_progress({'stage': 'finalizing'})
                finalize_output(raw, audio, output, duration,
                                media_idle_timeout=self.settings['media_idle_timeout'])
            except Exception:
                self._stop()
                raw.unlink(missing_ok=True)
                # Reinitialize before accepting the next queued job. Failed jobs
                # remain failed; they are never silently retried.
                try:
                    self.start()
                except Exception:
                    logger.exception('MuseTalk recovery failed; supervisor will retry')
                raise
