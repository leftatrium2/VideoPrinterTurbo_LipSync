"""GPU-free lifecycle and timeline checks. Fake worker does NOT test lip quality."""
import hashlib
import io
import json
import os
import subprocess
import time
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import create_app
from lipsync_service import model_assets
from lipsync_service.musetalk_engine import MuseTalkEngine
from lipsync_service.musetalk_runtime import audio_window_indices, prepare_workspace, video_timing


def copying_worker(connection, settings):
    if os.name == 'posix':
        os.setsid()
    connection.send(('ready', None))
    while True:
        request = connection.recv()
        if request is None:
            connection.close()
            return
        video, audio, output = request
        marker = Path(settings['runtime']) / 'failed-once'
        if settings['gpu'] in ('timeout', 'crash', 'error') and not marker.exists():
            marker.touch()
            if settings['gpu'] == 'timeout':
                time.sleep(60)
            elif settings['gpu'] == 'crash':
                os._exit(2)
            else:
                connection.send(('error', 'Test inference failure'))
                continue
        if settings.get('timeline') == 'audio':
            from lipsync_service.engine import probe
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-stream_loop', '-1', '-i', video,
                            '-t', str(probe(Path(audio), 'audio')), '-map', '0:v:0', '-c:v', 'libx264', output],
                           check=True)
        else:
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', video,
                            '-map', '0:v:0', '-c:v', 'copy', output], check=True)
        Path(output).with_name('worker-pid.txt').write_text(str(os.getpid()))
        connection.send(('done', None))


def failed_worker(connection, settings):
    connection.send(('error', 'Test initialization failure'))
    # Keep the test process alive until its parent handles the failure; Windows
    # must attach the Job Object before the intentionally failing target exits.
    try:
        connection.recv()
    finally:
        connection.close()


@pytest.fixture
def media(tmp_path):
    video, audio = tmp_path / 'source.mp4', tmp_path / 'voice.wav'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=160x120:rate=25:duration=1', str(video)], check=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=1', str(audio)], check=True)
    return video, audio


def test_worker_reuses_loaded_process_and_closes(tmp_path, media):
    engine = MuseTalkEngine(worker_target=copying_worker, runtime_dir=tmp_path / 'worker',
                            timeout=10, startup_timeout=10)
    try:
        engine.start()
        pid = engine._process.pid
        for index in range(2):
            directory = tmp_path / str(index)
            directory.mkdir()
            engine.run(*media, directory / 'result.mp4')
            assert int((directory / 'worker-pid.txt').read_text()) == pid
            assert (directory / 'result.mp4').stat().st_size > 0
            assert not (directory / 'model-output.mp4').exists()
    finally:
        engine.close()
    assert not engine.ready
    assert engine._process is None


def test_audio_timeline_accepts_different_durations_and_validates_against_audio(tmp_path, media):
    from lipsync_service.engine import probe
    video, _ = media
    audio = tmp_path / 'longer.wav'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=2', str(audio)], check=True)
    engine = MuseTalkEngine(worker_target=copying_worker, runtime_dir=tmp_path / 'worker',
                            timeout=10, startup_timeout=10, timeline='audio')
    from lipsync_service.engine import validate_inputs
    with pytest.raises(ValueError, match='maximum duration difference 150ms'):
        validate_inputs(video, audio)
    try:
        engine.run(video, audio, tmp_path / 'result.mp4')
        assert abs(probe(tmp_path / 'result.mp4', 'video') - 2) < .05
        assert abs(probe(tmp_path / 'result.mp4', 'audio') - 2) < .05
        assert abs(probe(video, 'video') - 1) < .05
    finally:
        engine.close()


@pytest.mark.parametrize('mode,exception', [('timeout', TimeoutError), ('crash', RuntimeError),
                                          ('error', RuntimeError)])
def test_failed_worker_is_replaced_and_next_job_runs(tmp_path, media, mode, exception):
    engine = MuseTalkEngine(worker_target=copying_worker, runtime_dir=tmp_path / 'worker',
                            gpu=mode, timeout=.2 if mode == 'timeout' else 10, startup_timeout=10)
    try:
        engine.start()
        original_pid = engine._process.pid
        with pytest.raises(exception):
            engine.run(*media, tmp_path / 'result.mp4')
        assert engine.ready
        assert engine._process.pid != original_pid
        engine.timeout = 10
        engine.run(*media, tmp_path / 'result.mp4')
        assert (tmp_path / 'result.mp4').stat().st_size
    finally:
        engine.close()


def test_startup_failure_releases_storage_lock(tmp_path):
    failed = MuseTalkEngine(worker_target=failed_worker, runtime_dir=tmp_path / 'worker',
                            startup_timeout=10)
    with pytest.raises(RuntimeError, match='initialization failure'):
        with TestClient(create_app(engine=failed, storage=tmp_path / 'storage')):
            pass
    assert not failed.ready
    from lipsync_service.engine import CommandEngine
    with TestClient(create_app(engine=CommandEngine([], 1), storage=tmp_path / 'storage')) as client:
        assert client.get('/').status_code == 200
        response = client.get('/health')
        assert response.status_code == 503
        assert response.json() == {'ready': False}


def test_idle_worker_crash_recovers_without_a_new_job(tmp_path):
    engine = MuseTalkEngine(worker_target=copying_worker, runtime_dir=tmp_path / 'worker',
                            startup_timeout=10)
    try:
        engine.start()
        original_pid = engine._process.pid
        engine._process.kill()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if engine.ready and engine._process.pid != original_pid:
                break
            time.sleep(.05)
        assert engine.ready and engine._process.pid != original_pid
        with TestClient(create_app(engine=engine, storage=tmp_path / 'storage')) as client:
            assert client.get('/health').json() == {'ready': True}
    finally:
        engine.close()


def test_fractional_fps_does_not_accumulate_audio_drift():
    fps = Fraction(30000, 1001)
    indices = audio_window_indices(18000, fps)
    assert len(indices) == 18000
    assert indices[0] == 0
    assert indices[-1] == int(17999 * 50 / fps)
    assert abs(indices[-1] / 50 - 17999 / float(fps)) < .02


def test_audio_features_keep_official_context_alignment():
    torch = pytest.importorskip('torch')
    from lipsync_service.musetalk_runtime import MuseTalkRuntime
    runtime = MuseTalkRuntime.__new__(MuseTalkRuntime)
    runtime.torch, runtime.device, runtime.dtype = torch, 'cpu', torch.float32
    runtime.audio_processor = SimpleNamespace(get_audio_feature=lambda path: ([torch.zeros(1)], 16000))
    hidden = torch.arange(1500, dtype=torch.float32).reshape(1, 1500, 1).repeat(1, 1, 3)
    runtime.whisper = SimpleNamespace(encoder=lambda *a, **kw: SimpleNamespace(hidden_states=(hidden, hidden + 100)))
    encoded = torch.stack((hidden[:, :50], hidden[:, :50] + 100), dim=2)
    padded = torch.cat((torch.zeros(1, 4, 2, 3), encoded, torch.zeros(1, 12, 2, 3)), dim=1)
    for fps, count in ((25, 25), (Fraction(30000, 1001), 30)):
        chunks = runtime._audio_features(Path('unused.wav'), count, fps)
        reference = torch.cat([padded[:, int(i * 50 / fps):int(i * 50 / fps) + 10]
                               for i in range(count)], dim=0).reshape(count, 20, 3)
        assert torch.equal(chunks, reference)


@pytest.mark.parametrize('timeline,audio_seconds,expected_count', [('strict', None, 30),
                                                                 ('audio', 2.1, 62), ('audio', .6, 17)])
def test_runtime_preserves_fractional_frame_count_and_rejects_missing_face(tmp_path, timeline,
                                                                         audio_seconds, expected_count):
    torch = pytest.importorskip('torch')
    cv2 = pytest.importorskip('cv2')
    import numpy as np
    from lipsync_service.musetalk_runtime import MuseTalkRuntime
    video, output = tmp_path / 'source with spaces.mp4', tmp_path / 'output.mp4'
    audio = tmp_path / 'voice.wav'
    if audio_seconds is not None:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        f'sine=frequency=440:duration={audio_seconds}', str(audio)], check=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=160x120:rate=30000/1001:duration=1', str(video)], check=True)
    runtime = MuseTalkRuntime.__new__(MuseTalkRuntime)
    runtime.torch, runtime.dtype, runtime.batch_size = torch, torch.float32, 4
    runtime.timeline = timeline
    runtime.timesteps, runtime.fp = torch.tensor([0]), None
    runtime.no_face = (0., 0., 0., 0.)
    original_frames, visited_frames = [], []
    def landmarks(paths, shift):
        frames = [cv2.imread(path) for path in paths]
        original_frames.extend(frame.tobytes() for frame in frames)
        return [(20, 20, 100, 80)] * len(paths), frames
    runtime.get_landmarks = landmarks
    runtime.vae = SimpleNamespace(get_latents_for_unet=lambda crop: torch.zeros(1, 8, 2, 2),
                                  decode_latents=lambda latent: np.zeros((len(latent), 256, 256, 3), dtype=np.uint8))
    batch_sizes = []
    def predict(latent, *args, **kw):
        batch_sizes.append(len(latent))
        return SimpleNamespace(sample=latent)
    runtime.unet = SimpleNamespace(model=predict)
    runtime.pe = lambda value: value
    runtime._audio_features = lambda audio, count, fps: torch.zeros(count, 50, 384)
    def blend(original, face, box, **kwargs):
        visited_frames.append(original.tobytes())
        return original
    runtime.get_image = blend
    runtime.run(video, audio, output)
    assert video_timing(video) == (Fraction(30000, 1001), 30)
    assert video_timing(output) == (Fraction(30000, 1001), expected_count)
    assert sum(batch_sizes) == expected_count and max(batch_sizes) <= 4
    cycle = original_frames + original_frames[::-1]
    assert visited_frames == [cycle[i % len(cycle)] for i in range(expected_count)]
    runtime.get_landmarks = lambda paths, shift: ([runtime.no_face] * len(paths),
                                                 [cv2.imread(path) for path in paths])
    with pytest.raises(ValueError, match='no detectable face'):
        runtime.run(video, audio, tmp_path / 'must-not-exist.mp4')
    assert not (tmp_path / 'must-not-exist.mp4').exists()


def test_timing_rejects_vfr_and_preserves_fractional_rate(monkeypatch):
    from lipsync_service import musetalk_runtime
    info = {'streams': [{'avg_frame_rate': '30000/1001'}],
            'frames': [{'best_effort_timestamp_time': str(i * 1001 / 30000)} for i in range(20)]}
    class Result:
        @property
        def stdout(self):
            return json.dumps(info)
    monkeypatch.setattr(musetalk_runtime.subprocess, 'run', lambda *a, **kw: Result())
    assert video_timing('ignored.mp4') == (Fraction(30000, 1001), 20)
    info['frames'][10]['best_effort_timestamp_time'] = '0.4'
    with pytest.raises(ValueError, match='constant frame rate'):
        video_timing('ignored.mp4')


def test_workspace_is_independent_of_launch_directory(tmp_path):
    source, models, workspace = [tmp_path / name for name in ('source', 'models', 'workspace')]
    (source / 'musetalk').mkdir(parents=True)
    models.mkdir()
    cwd = Path.cwd()
    prepare_workspace(source, models, workspace)
    prepare_workspace(source, models, workspace)
    assert (workspace / 'models').resolve() == models
    assert (workspace / 'musetalk').resolve() == source / 'musetalk'
    assert Path.cwd() == cwd


def test_model_download_resumes_and_rejects_corruption(tmp_path, monkeypatch):
    content = b'expected model bytes'
    spec = {'path': 'model.bin', 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
            'url': 'https://example.invalid/model'}
    (tmp_path / 'model.bin.part').write_bytes(content[:5])
    class Response(io.BytesIO):
        status = 206
        headers = {'Content-Range': f'bytes 5-{len(content)-1}/{len(content)}'}
    def open_url(request, timeout):
        assert request.get_header('Range') == 'bytes=5-'
        return Response(content[5:])
    monkeypatch.setattr(model_assets.urllib.request, 'urlopen', open_url)
    model_assets.download_asset(tmp_path, spec, 'ignored')
    assert (tmp_path / 'model.bin').read_bytes() == content
    assert model_assets.check_asset(tmp_path / 'model.bin', spec)
    (tmp_path / 'model.bin').write_bytes(b'corrupt')
    assert not model_assets.check_asset(tmp_path / 'model.bin', spec)


def test_download_html_is_never_published_as_a_model(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        status = 200
        headers = {}
    monkeypatch.setattr(model_assets.urllib.request, 'urlopen', lambda *a, **kw: Response(b'<html>denied</html>'))
    monkeypatch.setattr(model_assets.time, 'sleep', lambda seconds: None)
    spec = {'path': 'model.bin', 'url': 'https://example.invalid/model', 'sha256': '0' * 64}
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        model_assets.download_asset(tmp_path, spec, 'ignored')
    assert not (tmp_path / 'model.bin').exists()


def test_vendored_source_matches_pinned_manifest():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'third_party' / 'MuseTalk.source.json').read_text())
    for name, expected in manifest['files'].items():
        assert hashlib.sha256((root / 'third_party' / 'MuseTalk' / name).read_bytes()).hexdigest() == expected
