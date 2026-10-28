"""Media validation and command failures, without external processes."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lipsync_service import engine


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_digest_empty_and_multiple_chunks(self):
        path = self.root / 'input'
        for data in (b'', b'x' * (1024 * 1024 + 17)):
            path.write_bytes(data)
            self.assertEqual(engine.digest(path), hashlib.sha256(data).hexdigest())

    def test_probe_stream_duration_and_format_fallback(self):
        for stream in ({'codec_type': 'video', 'duration': '2.5'}, {'codec_type': 'video'}):
            payload = {'streams': [stream], 'format': {'duration': '3.5'}}
            with patch.object(engine.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(payload))):
                self.assertEqual(engine.probe(Path('input'), 'video'), 2.5 if 'duration' in stream else 3.5)

    def test_probe_rejects_invalid_duration_and_propagates_decoder_failure(self):
        for duration in ('0', '-1', 'nan', 'inf'):
            payload = {'streams': [{'codec_type': 'audio', 'duration': duration}]}
            with self.subTest(duration=duration), patch.object(engine.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(payload))):
                with self.assertRaisesRegex(ValueError, 'Invalid audio duration'):
                    engine.probe(Path('input'), 'audio')
        with patch.object(engine.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'ffprobe')):
            with self.assertRaises(subprocess.CalledProcessError):
                engine.probe(Path('input'), 'video')

    def test_timeline_modes_and_rejection(self):
        for mode, expected in [('strict', 1), ('audio', 1.1)]:
            with patch.object(engine, 'probe', side_effect=[1, 1.1]):
                self.assertEqual(engine.validate_inputs(Path('v'), Path('a'), mode), expected)
        with patch.object(engine, 'probe', side_effect=[1, 2]):
            with self.assertRaisesRegex(ValueError, '150ms'):
                engine.validate_inputs(Path('v'), Path('a'))
        with patch.object(engine, 'probe') as probe:
            with self.assertRaises(ValueError):
                engine.validate_inputs(Path('v'), Path('a'), 'invalid')
            probe.assert_not_called()

    def test_finalize_checks_raw_and_only_removes_it_after_validation(self):
        raw, audio, output = [self.root / name for name in ('raw.mp4', 'audio.wav', 'output.mp4')]
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            engine.finalize_output(raw, audio, output, 1)
        raw.touch()
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            engine.finalize_output(raw, audio, output, 1)
        raw.write_bytes(b'video')
        with patch.object(engine, 'probe', return_value=2), patch.object(engine, 'ffmpeg') as encode:
            with self.assertRaisesRegex(ValueError, 'changed the video timeline'):
                engine.finalize_output(raw, audio, output, 1)
            encode.assert_not_called()
        for durations, message in [([1, 2], 'Final output duration mismatch')]:
            with patch.object(engine, 'probe', side_effect=durations), patch.object(engine, 'ffmpeg'):
                with self.assertRaisesRegex(ValueError, message):
                    engine.finalize_output(raw, audio, output, 1)
                self.assertTrue(raw.exists())
        with patch.object(engine, 'probe', side_effect=[1, 1, 1]), patch.object(engine, 'ffmpeg') as encode:
            engine.finalize_output(raw, audio, output, 1)
            self.assertEqual(encode.call_count, 3)
            self.assertFalse(raw.exists())

    def test_command_failure_never_finalizes_and_preserves_argument_boundaries(self):
        process = Mock(pid=123)
        process.wait.return_value = 7
        command = engine.CommandEngine(['model', '{video}', '{audio}', '{output}'], 15)
        with patch.object(engine, 'validate_inputs', return_value=1), \
             patch.object(engine.subprocess, 'Popen', return_value=process) as spawn, \
             patch.object(engine, 'finalize_output') as finalize:
            with self.assertRaises(subprocess.CalledProcessError):
                command.run(self.root / 'video with spaces.mp4', self.root / 'audio.wav', self.root / 'result.mp4')
            self.assertEqual(spawn.call_args.args[0][1], str(self.root / 'video with spaces.mp4'))
            self.assertNotIn('shell', spawn.call_args.kwargs)
            finalize.assert_not_called()
            self.assertTrue((self.root / 'inference.log').exists())
