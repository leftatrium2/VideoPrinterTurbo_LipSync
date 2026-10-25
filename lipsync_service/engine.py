"""Explicit model command integration; never substitutes a fake LipSync model."""
import hashlib
import json
import math
import os
import signal
import subprocess
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def probe(path: Path, kind: str) -> float:
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
        capture_output=True, check=True, timeout=60, text=True)
    data = json.loads(result.stdout)
    stream = next(s for s in data['streams'] if s.get('codec_type') == kind)
    duration = float(stream.get('duration') or data['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f'Invalid {kind} duration')
    return duration


def ffmpeg(args):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', *map(str, args)],
                   capture_output=True, check=True, timeout=1800)


def validate_inputs(video: Path, audio: Path, timeline='strict') -> float:
    if timeline not in ('strict', 'audio'):
        raise ValueError('Timeline must be strict or audio')
    duration = probe(video, 'video')
    audio_duration = probe(audio, 'audio')
    if timeline == 'audio':
        return audio_duration
    if abs(duration - audio_duration) > .15:
        raise ValueError(f'Video ({duration:.3f}s) and audio ({audio_duration:.3f}s) must share a timeline '
                         '(maximum duration difference 150ms)')
    return duration


def finalize_output(raw: Path, audio: Path, output: Path, duration: float):
    if not raw.is_file() or not raw.stat().st_size:
        raise ValueError('Model did not create a nonempty output MP4')
    if abs(probe(raw, 'video') - duration) > .15:
        raise ValueError('Model changed the video timeline')
    ffmpeg(['-i', raw, '-map', '0:v:0', '-f', 'null', '-'])
    ffmpeg(['-i', raw, '-i', audio, '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'copy', '-c:a', 'aac', '-movflags', '+faststart', output])
    if abs(probe(output, 'video') - duration) > .15:
        raise ValueError('Final output duration mismatch')
    probe(output, 'audio')
    ffmpeg(['-i', output, '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    raw.unlink()


class CommandEngine:
    def __init__(self, command: list[str], timeout: float):
        self.command, self.timeout = command, timeout

    @property
    def ready(self):
        return bool(self.command)

    def run(self, video: Path, audio: Path, output: Path):
        duration = validate_inputs(video, audio)
        raw = output.with_name('model-output.mp4')
        values = {'video': str(video), 'audio': str(audio), 'output': str(raw)}
        # No shell, and no client-supplied flags or commands.
        command = [arg.format_map(values) for arg in self.command]
        with output.with_name('inference.log').open('wb') as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != 'nt')
            try:
                code = process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                # Terminate model children as well, so the next job cannot overlap on GPU.
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, timeout=30)
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
                raise
            if code:
                raise subprocess.CalledProcessError(code, command)
        finalize_output(raw, audio, output, duration)
