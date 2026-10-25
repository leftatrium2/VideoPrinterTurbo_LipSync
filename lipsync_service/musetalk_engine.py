"""Persistent Python worker; CUDA state never enters the HTTP process."""
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .engine import finalize_output, validate_inputs

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


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
        try:
            # On Windows the parent attaches a kill-on-close Job Object before
            # this gate opens, so model initialization cannot orphan children.
            if connection.recv() != ('initialize', None):
                raise RuntimeError('Invalid MuseTalk initialization handshake')
            from .musetalk_runtime import MuseTalkRuntime
            runtime = MuseTalkRuntime(settings)
            connection.send(('ready', None))
            while True:
                request = connection.recv()
                if request is None:
                    break
                video, audio, output = map(Path, request)
                with output.with_name('inference.log').open('a', buffering=1) as task_log:
                    os.dup2(task_log.fileno(), 1)
                    os.dup2(task_log.fileno(), 2)
                    try:
                        runtime.run(video, audio, output)
                        connection.send(('done', None))
                    except Exception:
                        logger.exception('MuseTalk inference failed')
                        connection.send(('error', 'MuseTalk inference failed; inspect inference.log'))
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


class MuseTalkEngine:
    def __init__(self, *, model_dir=None, gpu='0', batch_size=1, use_float16=True,
                 timeout=3600, startup_timeout=600, runtime_dir=None, worker_target=None, timeline='strict'):
        if batch_size < 1:
            raise ValueError('MuseTalk batch size must be positive')
        if timeline not in ('strict', 'audio'):
            raise ValueError('Timeline must be strict or audio')
        self.timeout, self.startup_timeout = timeout, startup_timeout
        self.settings = {
            'source': str(ROOT / 'third_party' / 'MuseTalk'),
            'models': str(Path(model_dir or ROOT / 'models').resolve()),
            'runtime': str(Path(runtime_dir or ROOT / '.runtime' / 'musetalk').resolve()),
            'gpu': str(gpu), 'batch_size': batch_size, 'float16': use_float16, 'timeline': timeline,
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

    def _receive(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._connection.poll(min(.1, max(0, deadline - time.monotonic()))):
                try:
                    return self._connection.recv()
                except (EOFError, OSError) as exc:
                    raise RuntimeError('MuseTalk worker exited unexpectedly') from exc
            if not self._process.is_alive():
                raise RuntimeError('MuseTalk worker exited unexpectedly')
        raise TimeoutError('MuseTalk worker timed out')

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

    def _stop(self):
        self._loaded = False
        process, self._process = self._process, None
        group_id, self._group_id = self._group_id, None
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
            self._stop()
        if self._watcher is not None:
            self._watcher.join(timeout=10)

    def run(self, video: Path, audio: Path, output: Path):
        video, audio, output = video.resolve(), audio.resolve(), output.resolve()
        duration = validate_inputs(video, audio, self.settings['timeline'])
        raw = output.with_name('model-output.mp4')
        with self._lock:
            self.start()
            try:
                self._connection.send(tuple(map(str, (video, audio, raw))))
                status, detail = self._receive(self.timeout)
                if status != 'done':
                    raise RuntimeError(detail)
                finalize_output(raw, audio, output, duration)
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
