import logging
import queue
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException

from .engine import digest, validate_inputs
from .storage import StorageLock

logger = logging.getLogger(__name__)


@dataclass
class Upload:
    id: str
    directory: Path
    files: dict
    touched: float = field(default_factory=time.monotonic)
    job_id: str | None = None
    readers: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def view(self):
        return {'upload_id': self.id, 'files': {
            k: {**v['meta'], 'offset': v['path'].stat().st_size} for k, v in self.files.items()
        }, 'job_id': self.job_id}


class Service:
    def __init__(self, engine, storage: Path, ttl=86400, capacity=32, max_file=5 * 1024**3,
                 chunk_size=8 * 1024**2):
        self.engine, self.ttl, self.capacity = engine, ttl, capacity
        self.max_file, self.chunk_size = max_file, chunk_size
        storage.mkdir(parents=True, exist_ok=True)
        self.storage_lock = StorageLock(storage / '.service.lock')
        try:
            # The process lock guarantees these are abandoned runs, never active uploads.
            for path in storage.iterdir():
                if re.fullmatch(r'lipsync-[a-z0-9_]{8}', path.name) and path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
            self.temp = tempfile.TemporaryDirectory(prefix='lipsync-', dir=storage)
        except Exception:
            self.storage_lock.close()
            raise
        self.root = Path(self.temp.name)
        self.uploads, self.jobs = {}, {}
        self.lock = threading.RLock()
        self.queue = queue.Queue()
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._work, name='lipsync-gpu', daemon=True)
        self.cleaner = threading.Thread(target=self._clean, name='lipsync-cleanup', daemon=True)

    def start(self):
        self.worker.start()
        self.cleaner.start()

    def close(self):
        self.stop.set()
        if self.worker.ident is not None:
            self.worker.join()
        if self.cleaner.ident is not None:
            self.cleaner.join()
        try:
            if hasattr(self.engine, 'close'):
                self.engine.close()
        finally:
            try:
                self.temp.cleanup()
            finally:
                self.storage_lock.close()

    def create(self, payload):
        with self.lock:
            if len(self.uploads) >= self.capacity:
                raise HTTPException(429, 'Upload capacity exhausted; retry after cleanup')
            uid = uuid.uuid4().hex
            directory = self.root / uid
            directory.mkdir()
            files = {}
            for kind, spec in payload.items():
                if spec['size'] > self.max_file:
                    shutil.rmtree(directory)
                    raise HTTPException(413, 'Input file too large')
                path = directory / (kind + Path(spec['name']).suffix.lower())
                path.touch()
                files[kind] = {'meta': {'size': spec['size'], 'sha256': spec['sha256']}, 'path': path}
            upload = Upload(uid, directory, files)
            self.uploads[uid] = upload
            return upload.view()

    def get(self, uid):
        with self.lock:
            upload = self.uploads.get(uid)
            if upload is None:
                raise HTTPException(404, 'Upload expired, unknown, or service restarted')
            upload.touched = time.monotonic()
            return upload

    def view(self, uid):
        with self.lock:
            upload = self.get(uid)
            if not upload.lock.acquire(blocking=False):
                raise HTTPException(409, 'Upload operation in progress')
            try:
                return upload.view()
            finally:
                upload.lock.release()

    def job(self, jid):
        with self.lock:
            job = self.jobs.get(jid)
            if job is None:
                raise HTTPException(404, 'Job expired, unknown, or service restarted')
            self.get(job['upload_id'])
            return dict(job)

    def submit(self, uid, timeline='strict'):
        if timeline not in ('strict', 'audio'):
            raise HTTPException(422, 'Timeline must be strict or audio')
        with self.lock:
            upload = self.get(uid)
            if not upload.lock.acquire(blocking=False):
                raise HTTPException(409, 'Upload operation in progress')
        try:
            if upload.job_id:
                job = self.job(upload.job_id)
                if job['timeline'] != timeline:
                    raise HTTPException(409, 'Upload was already submitted with a different timeline')
                return job
            if timeline == 'audio' and not hasattr(self.engine, 'run_timeline'):
                raise HTTPException(422, 'This engine does not support the audio timeline')
            if not self.engine.ready:
                raise HTTPException(503, 'LipSync engine is not ready')
            # Hash outside the global lock: multi-GiB inputs must not block other sessions.
            for item in upload.files.values():
                if item['path'].stat().st_size != item['meta']['size']:
                    raise HTTPException(409, 'Upload incomplete')
                if digest(item['path']) != item['meta']['sha256']:
                    raise HTTPException(422, 'Input SHA-256 mismatch; create a new upload')
            with self.lock:
                jid = uuid.uuid4().hex
                job = {'job_id': jid, 'upload_id': uid, 'status': 'queued', 'error': None, 'result': None,
                       'timeline': timeline, 'expected_duration': None}
                self.jobs[jid] = job
                upload.job_id = jid
                self.queue.put(jid)
                return dict(job)
        finally:
            upload.lock.release()

    def cleanup(self):
        with self.lock:
            for uid, upload in list(self.uploads.items()):
                if upload.readers or time.monotonic() - upload.touched < self.ttl:
                    continue
                if upload.job_id and self.jobs[upload.job_id]['status'] in ('queued', 'running'):
                    continue
                if not upload.lock.acquire(blocking=False):
                    continue
                try:
                    shutil.rmtree(upload.directory)
                    self.uploads.pop(uid)
                    if upload.job_id:
                        self.jobs.pop(upload.job_id)
                finally:
                    upload.lock.release()

    def _clean(self):
        while not self.stop.wait(min(60, max(.1, self.ttl / 2))):
            try:
                self.cleanup()
            except OSError:
                logger.exception('LipSync temporary file cleanup failed')

    def _work(self):
        while not self.stop.is_set():
            try:
                jid = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            with self.lock:
                job = self.jobs[jid]
                upload = self.uploads[job['upload_id']]
                job['status'] = 'running'
            output = upload.directory / 'result.mp4'
            try:
                video, audio = upload.files['video']['path'], upload.files['audio']['path']
                expected = validate_inputs(video, audio, job['timeline'])
                with self.lock:
                    job['expected_duration'] = expected
                if hasattr(self.engine, 'run_timeline'):
                    self.engine.run_timeline(video, audio, output, timeline=job['timeline'])
                else:
                    self.engine.run(video, audio, output)
                if not output.is_file() or not output.stat().st_size:
                    raise ValueError('Engine returned an empty output')
                result = {'size': output.stat().st_size, 'sha256': digest(output)}
                with self.lock:
                    job.update(status='succeeded', result=result)
            except Exception as exc:
                logger.exception('LipSync job %s failed', jid)
                output.unlink(missing_ok=True)
                with self.lock:
                    # Do not expose command arguments, paths, or subprocess logs over HTTP.
                    detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    job.update(status='failed', error=detail[:500])
            finally:
                with self.lock:
                    upload.touched = time.monotonic()
                self.queue.task_done()
