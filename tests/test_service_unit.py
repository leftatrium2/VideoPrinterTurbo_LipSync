"""Session lifecycle and cleanup guards without HTTP or GPU."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from lipsync_service.service import Service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = Service(SimpleNamespace(ready=True), Path(self.temp.name), capacity=2, max_file=8)
        self.addCleanup(self.service.close)
        self.payload = {kind: {'name': name, 'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}
                        for kind, name in [('video', 'video.MP4'), ('audio', 'audio.wav')]}

    def upload(self, complete=True):
        view = self.service.create(self.payload)
        upload = self.service.get(view['upload_id'])
        if complete:
            for item in upload.files.values():
                item['path'].write_bytes(b'abc')
        return upload

    def test_capacity_and_oversize_leave_no_partial_upload(self):
        self.payload['audio']['size'] = 9
        with self.assertRaises(HTTPException) as caught:
            self.service.create(self.payload)
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(list(self.service.root.iterdir()), [])
        self.payload['audio']['size'] = 3
        self.upload()
        self.upload()
        with self.assertRaises(HTTPException) as caught:
            self.upload()
        self.assertEqual(caught.exception.status_code, 429)

    def test_submit_rejects_incomplete_corrupt_and_unready(self):
        upload = self.upload(complete=False)
        for data, status in [(b'ab', 409), (b'bad', 422)]:
            for item in upload.files.values():
                item['path'].write_bytes(data)
            with self.assertRaises(HTTPException) as caught:
                self.service.submit(upload.id)
            self.assertEqual(caught.exception.status_code, status)
            self.assertIsNone(upload.job_id)
            self.assertFalse(upload.lock.locked())
        self.service.engine.ready = False
        with self.assertRaises(HTTPException) as caught:
            self.service.submit(upload.id)
        self.assertEqual(caught.exception.status_code, 503)

    def test_submit_is_idempotent_and_conflicting_mode_rejected(self):
        upload = self.upload()
        first = self.service.submit(upload.id)
        self.assertEqual(self.service.submit(upload.id), first)
        self.assertEqual(self.service.queue.qsize(), 1)
        with self.assertRaises(HTTPException) as caught:
            self.service.submit(upload.id, 'audio')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertFalse(upload.lock.locked())

    def test_cleanup_preserves_active_readers_jobs_and_locked_uploads(self):
        upload = self.upload()
        job = self.service.submit(upload.id)
        upload.touched = 0
        with patch('lipsync_service.service.time.monotonic', return_value=1e9):
            for state in ('queued', 'running'):
                self.service.jobs[job['job_id']]['status'] = state
                self.service.cleanup()
                self.assertTrue(upload.directory.exists())
            self.service.jobs[job['job_id']]['status'] = 'failed'
            upload.readers = 1
            self.service.cleanup()
            self.assertTrue(upload.directory.exists())
            upload.readers = 0
            upload.lock.acquire()
            self.service.cleanup()
            upload.lock.release()
            self.assertTrue(upload.directory.exists())
            self.service.cleanup()
        self.assertFalse(upload.directory.exists())
        self.assertNotIn(upload.id, self.service.uploads)
        self.assertNotIn(job['job_id'], self.service.jobs)

    def test_view_conflict_unknown_ids_and_returned_job_is_copy(self):
        upload = self.upload()
        upload.lock.acquire()
        try:
            with self.assertRaises(HTTPException) as caught:
                self.service.view(upload.id)
            self.assertEqual(caught.exception.status_code, 409)
        finally:
            upload.lock.release()
        job = self.service.submit(upload.id)
        job['status'] = 'tampered'
        self.assertEqual(self.service.job(job['job_id'])['status'], 'queued')
        for lookup in (self.service.get, self.service.job):
            with self.assertRaises(HTTPException) as caught:
                lookup('missing')
            self.assertEqual(caught.exception.status_code, 404)

    def test_worker_publishes_only_nonempty_results_and_sanitizes_errors(self):
        for outcome in ('success', 'empty', 'exception'):
            with self.subTest(outcome=outcome):
                upload = self.upload()
                job = self.service.submit(upload.id)

                def run(video, audio, output):
                    if outcome == 'exception':
                        output.write_bytes(b'partial')
                        raise RuntimeError('secret command /private/path')
                    output.write_bytes(b'result' if outcome == 'success' else b'')

                self.service.engine.run = run
                with patch.object(self.service.stop, 'is_set', side_effect=[False, True]), \
                     patch('lipsync_service.service.validate_inputs', return_value=1), \
                     patch('lipsync_service.service.logger'):
                    self.service._work()
                actual = self.service.job(job['job_id'])
                if outcome == 'success':
                    self.assertEqual(actual['status'], 'succeeded')
                    self.assertEqual(actual['result'], {'size': 6, 'sha256': hashlib.sha256(b'result').hexdigest()})
                else:
                    self.assertEqual(actual['status'], 'failed')
                    self.assertIsNone(actual['result'])
                    self.assertFalse((upload.directory / 'result.mp4').exists())
                    if outcome == 'exception':
                        self.assertEqual(actual['error'], 'RuntimeError')
                self.assertEqual(self.service.queue.unfinished_tasks, 0)
                # Free capacity while keeping each outcome isolated.
                upload.touched = 0
                with patch('lipsync_service.service.time.monotonic', return_value=1e9):
                    self.service.cleanup()
