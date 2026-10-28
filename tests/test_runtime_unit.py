"""Runtime timeline, workspace and worker protocol edge cases without CUDA."""
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lipsync_service import musetalk_runtime as runtime
from lipsync_service.musetalk_engine import MuseTalkEngine


class RuntimeTests(unittest.TestCase):
    def test_frame_indices_pingpong_and_short_inputs(self):
        self.assertEqual(runtime.output_frame_indices(3, 2), [0, 1, 2])
        self.assertEqual(runtime.output_frame_indices(3, 2, 4), [0, 1, 2, 2, 1, 0, 0, 1])
        for count, fps, duration in [(0, 30, None), (2, 30, .001), (3, 1, 1.9)]:
            with self.subTest(count=count, fps=fps, duration=duration), self.assertRaises(ValueError):
                runtime.output_frame_indices(count, fps, duration)
        indices = runtime.audio_window_indices(18000, Fraction(30000, 1001))
        self.assertLess(abs(indices[-1] / 50 - 17999 * 1001 / 30000), .02)

    def test_video_timing_rejects_bad_timestamps(self):
        for rate, timestamps in [('0/1', [0]), ('30/1', []), ('30/1', [0, 0]),
                                 ('30/1', [0, float('nan')]), ('30/1', [0, .1])]:
            info = {'streams': [{'avg_frame_rate': rate}],
                    'frames': [{'best_effort_timestamp_time': str(t)} for t in timestamps]}
            with self.subTest(rate=rate, timestamps=timestamps), \
                 patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(info))), \
                 self.assertRaises(ValueError):
                runtime.video_timing('video.mp4')

    def test_workspace_refuses_real_directory_and_replaces_stale_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, models, workspace = [root / name for name in ('source', 'models', 'workspace')]
            (source / 'musetalk').mkdir(parents=True)
            models.mkdir()
            workspace.mkdir()
            (workspace / 'musetalk').mkdir()
            with self.assertRaisesRegex(RuntimeError, 'Unexpected directory'):
                runtime.prepare_workspace(source, models, workspace)
            (workspace / 'musetalk').rmdir()
            (workspace / 'musetalk').symlink_to(root / 'missing', target_is_directory=True)
            runtime.prepare_workspace(source, models, workspace)
            runtime.prepare_workspace(source, models, workspace)
            self.assertEqual((workspace / 'musetalk').resolve(), (source / 'musetalk').resolve())
            self.assertEqual((workspace / 'models').resolve(), models.resolve())

    def test_engine_configuration_closed_state_and_dead_handle(self):
        for args in ({'batch_size': 0}, {'timeline': 'unknown'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                MuseTalkEngine(**args)
        engine = MuseTalkEngine()
        engine.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            engine.start()
        engine._loaded = True
        engine._process = Mock()
        engine._process.is_alive.side_effect = ValueError('closed handle')
        self.assertFalse(engine.ready)

    def test_receive_eof_and_worker_exit(self):
        for ready in (False, True):
            engine = MuseTalkEngine()
            engine._connection = Mock()
            engine._connection.poll.return_value = ready
            engine._connection.recv.side_effect = EOFError()
            engine._process = Mock()
            engine._process.is_alive.return_value = False
            with self.subTest(ready=ready), self.assertRaisesRegex(RuntimeError, 'exited unexpectedly'):
                engine._receive(1)
        with self.assertRaises(TimeoutError):
            MuseTalkEngine()._receive(0)
