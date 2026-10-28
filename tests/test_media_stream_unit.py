"""Real subprocess/FFmpeg checks for deadlines, frame order and resolution."""
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from lipsync_service.errors import InferenceError
from lipsync_service.media_stream import frame_blocks, lines, raw_frames, video_writer
from lipsync_service.musetalk_runtime import video_timing


class MediaStreamTests(unittest.TestCase):
    def test_probe_output_is_streamed_and_ongoing_progress_has_no_fixed_deadline(self):
        command = [sys.executable, '-u', '-c',
                   'import time\nfor i in range(6):\n print(i, flush=True)\n time.sleep(.03)']
        self.assertEqual(list(lines(command, stage='test probe', idle_timeout=.12)), list(map(str, range(6))))

    def test_stalled_process_is_killed_and_reaped(self):
        created = []
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            created.append(process)
            return process
        with patch('lipsync_service.media_stream.subprocess.Popen', side_effect=spawn):
            with self.assertRaisesRegex(InferenceError, 'test stage stalled'):
                list(lines([sys.executable, '-c', 'import time;time.sleep(10)'],
                           stage='test stage', idle_timeout=.1))
        self.assertTrue(all(process.poll() is not None for process in created))

    def test_lazy_planner_keeps_blocks_bounded_at_large_counts(self):
        blocks = frame_blocks(10**9, 10**12, 3)
        self.assertEqual(next(blocks), (0, [0, 1, 2]))
        self.assertEqual(next(blocks), (3, [3, 4, 5]))
        values = [i for _, indices in frame_blocks(3, 14, 2) for i in indices]
        self.assertEqual(values, [0, 1, 2, 2, 1, 0, 0, 1, 2, 2, 1, 0, 0, 1])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Requires ffmpeg/ffprobe')
    def test_fractional_seeks_match_forward_decode_at_block_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'fractional.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x120:rate=30000/1001:duration=2.1',
                            '-c:v', 'libx264', '-g', '15', str(video)], check=True)
            rate, count = video_timing(video)
            self.assertEqual(rate, Fraction(30000, 1001))
            reference = list(raw_frames(video, 160, 120, rate, 0, count))
            for first in (1, 14, 15, 16, count - 3):
                with self.subTest(first=first):
                    actual = list(raw_frames(video, 160, 120, rate, first, 3))
                    self.assertEqual(actual, reference[first:first + 3])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Requires ffmpeg/ffprobe')
    def test_encoder_preserves_4k_resolution_and_removes_failed_output(self):
        class Frame:
            shape = (2160, 3840, 3)
            def tobytes(self):
                return bytes(2160 * 3840 * 3)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / '4k.mp4'
            with video_writer(output, 3840, 2160, Fraction(24000, 1001)) as write:
                for _ in range(3):
                    write(Frame())
            self.assertEqual(video_timing(output), (Fraction(24000, 1001), 3))
            info = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                           '-show_entries', 'stream=width,height', '-of', 'csv=p=0', str(output)], text=True)
            self.assertEqual(info.strip(), '3840,2160')
            with self.assertRaisesRegex(RuntimeError, 'inference error'):
                with video_writer(output, 3840, 2160, Fraction(24)) as write:
                    write(Frame())
                    raise RuntimeError('inference error')
            self.assertFalse(output.exists())
