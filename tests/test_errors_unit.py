"""Detailed inference errors survive worker IPC and the job API boundary."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lipsync_service.errors import InferenceError, inference_detail
from lipsync_service.musetalk_engine import MuseTalkEngine, _worker
from lipsync_service.service import Service


class ErrorPropagationTests(unittest.TestCase):
    def test_diagnostics_are_bounded_and_subprocess_commands_are_omitted(self):
        for exc in (subprocess.CalledProcessError(7, ['secret-token']),
                    subprocess.TimeoutExpired(['secret-token'], 10),
                    FileNotFoundError(2, 'No such file', '/private/secret')):
            self.assertNotIn('secret', inference_detail(exc))
        self.assertEqual(inference_detail(ValueError('bad\nface')), 'ValueError: bad face')
        self.assertLessEqual(len(inference_detail(RuntimeError('x' * 10000))), 1500)

    def test_worker_engine_and_service_preserve_face_diagnostic(self):
        reason = 'Invalid face bounding box after clipping: frame 42 (0-based), box=(721, 10, 740, 100), image=720x1280'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = Mock()
            connection.recv.side_effect = [('initialize', None),
                                           (str(root / 'v.mp4'), str(root / 'a.wav'), str(root / 'raw.mp4'), 'strict'), None]
            runtime = Mock()
            runtime.run.side_effect = ValueError(reason)
            with patch.dict(os.environ), patch('lipsync_service.musetalk_engine.os.setsid', create=True), \
                 patch('lipsync_service.musetalk_engine.os.dup2'), \
                 patch('lipsync_service.musetalk_engine.logger'), \
                 patch('lipsync_service.musetalk_runtime.MuseTalkRuntime', return_value=runtime):
                _worker(connection, {'gpu': '0', 'runtime': directory})
            status, detail = connection.send.call_args_list[-1].args[0]
            self.assertEqual(status, 'error')
            self.assertIn(reason, detail)
            self.assertIn('ValueError', detail)
            self.assertIn('inference.log', detail)
            connection.close.assert_called_once()

            engine = MuseTalkEngine()
            engine._connection = Mock()
            with patch('lipsync_service.musetalk_engine.validate_inputs', return_value=1), \
                 patch.object(engine, 'start'), patch.object(engine, '_stop'), \
                 patch.object(engine, '_receive', return_value=(status, detail)):
                with self.assertRaises(InferenceError) as caught:
                    engine.run(root / 'v.mp4', root / 'a.wav', root / 'result.mp4')
            self.assertEqual(str(caught.exception), detail)

            service = Service(Mock(ready=True), root / 'storage')
            try:
                import hashlib
                payload = {kind: {'name': name, 'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}
                           for kind, name in [('video', 'v.mp4'), ('audio', 'a.wav')]}
                upload = service.get(service.create(payload)['upload_id'])
                for item in upload.files.values():
                    item['path'].write_bytes(b'x')
                job = service.submit(upload.id)
                service.engine.run_timeline.side_effect = InferenceError(detail)
                with patch.object(service.stop, 'is_set', side_effect=[False, True]), \
                     patch('lipsync_service.service.validate_inputs', return_value=1), \
                     patch('lipsync_service.service.logger'):
                    service._work()
                result = service.job(job['job_id'])
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['error'], detail)
                # This is the unchanged remote client's failure formatting.
                client_message = f"LipSync job {result['job_id']} failed: {result.get('error')}"
                self.assertIn(reason, client_message)
            finally:
                service.close()
