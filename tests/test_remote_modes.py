"""Real HTTP/media protocol tests; the FFmpeg fixture is not a lip-sync model."""
import json
import errno
import os
import sys
from pathlib import Path
import subprocess
import time

import pytest
import requests
from fastapi.testclient import TestClient

import test_transfer as transfer
from app import create_app
from lipsync_service.engine import ffmpeg, finalize_output, probe, validate_inputs
from pipeline.lip_sync.remote import RemoteLipSync

media = transfer.media


@pytest.fixture(params=['asgi', 'tcp'])
def transport(request):
    transfer.TRANSPORT = request.param


class TimelineFixture:
    ready = True

    def __init__(self):
        self.seen = []

    def run_timeline(self, video, audio, output, *, timeline='strict'):
        self.seen.append(timeline)
        duration = validate_inputs(video, audio, timeline)
        raw = output.with_name('fixture.mp4')
        options = ['-stream_loop', '-1'] if timeline == 'audio' else []
        ffmpeg([*options, '-i', video, '-t', str(duration), '-map', '0:v:0', '-c:v', 'libx264', raw])
        finalize_output(raw, audio, output, duration)


def make_audio(path, seconds):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    f'sine=duration={seconds}', str(path)], check=True)
    return path


def test_submit_modes_validation_and_idempotency(tmp_path, media):
    with TestClient(create_app(engine=TimelineFixture(), storage=tmp_path)) as c:
        assert c.get('/health').json()['timelines'] == ['strict', 'audio']
        uid = transfer.upload_all(c, media)
        assert c.post(f'/v1/uploads/{uid}/submit', json={'timeline': 'invalid'}).status_code == 422
        strict = c.post(f'/v1/uploads/{uid}/submit').json()['data']
        assert strict['timeline'] == 'strict'
        assert c.post(f'/v1/uploads/{uid}/submit').json()['data']['job_id'] == strict['job_id']
        assert c.post(f'/v1/uploads/{uid}/submit', json={'timeline': 'audio'}).status_code == 409
        done = transfer.wait(c, strict['job_id'])
        assert done['status'] == 'succeeded' and done['expected_duration'] == pytest.approx(1)
    with TestClient(create_app(engine=transfer.engine(), storage=tmp_path)) as c:
        uid = transfer.upload_all(c, media)
        assert c.post(f'/v1/uploads/{uid}/submit', json={'timeline': 'audio'}).status_code == 422


@pytest.mark.parametrize('seconds', [.5, 2])
def test_audio_client_preserves_complete_audio(tmp_path, media, transport, seconds):
    audio = make_audio(tmp_path / 'different.wav', seconds)
    fixture = TimelineFixture()
    app = create_app(engine=fixture, storage=tmp_path / 'server')
    with transfer.live(app) as url:
        with pytest.raises(ValueError, match='150ms'):
            transfer.client(url).lip_sync(str(media[0]), str(audio), str(tmp_path / 'strict.mp4'))
        output = tmp_path / 'audio.mp4'
        transfer.client(url, timeline='audio').lip_sync(str(media[0]), str(audio), str(output))
        assert probe(output, 'video') == pytest.approx(seconds, abs=.05)
        assert probe(output, 'audio') == pytest.approx(seconds, abs=.05)
        assert fixture.seen == ['audio']
        job = next(iter(app.state.service.jobs.values()))
        assert job['timeline'] == 'audio' and job['expected_duration'] == pytest.approx(seconds)


def test_switching_checkpoint_mode_creates_new_task(tmp_path, media, transport):
    fixture = TimelineFixture()
    app = create_app(engine=fixture, storage=tmp_path / 'server')
    output = tmp_path / 'result.mp4'
    with transfer.live(app) as url:
        first = transfer.client(url)
        first._download = lambda *args: (_ for _ in ()).throw(TimeoutError('Interrupted before download'))
        with pytest.raises(TimeoutError):
            first.lip_sync(*map(str, media), str(output))
        checkpoint = output.with_name(output.name + '.lipsync.json')
        old = json.loads(checkpoint.read_text())
        assert old['identity']['timeline'] == 'strict'
        transfer.client(url, timeline='audio').lip_sync(*map(str, media), str(output))
        assert len(app.state.service.jobs) == 2
        assert fixture.seen == ['strict', 'audio']


def test_legacy_strict_checkpoint_resumes_same_job(tmp_path, media, transport):
    app = create_app(engine=TimelineFixture(), storage=tmp_path / 'server')
    output = tmp_path / 'result.mp4'
    with transfer.live(app) as url:
        first = transfer.client(url)
        first._download = lambda *args: (_ for _ in ()).throw(TimeoutError('Interrupted before download'))
        with pytest.raises(TimeoutError):
            first.lip_sync(*map(str, media), str(output))
        checkpoint = output.with_name(output.name + '.lipsync.json')
        state = json.loads(checkpoint.read_text())
        del state['identity']['timeline']
        checkpoint.write_text(json.dumps(state))
        transfer.client(url).lip_sync(*map(str, media), str(output))
        assert len(app.state.service.jobs) == 1


def test_audio_submit_lost_ack_is_not_repeated_work(tmp_path, media, transport):
    app = create_app(engine=TimelineFixture(), storage=tmp_path / 'server')
    class LostAck(RemoteLipSync):
        lost = False

        def _request(self, method, path, **kwargs):
            response = super()._request(method, path, **kwargs)
            if method == 'POST' and path.endswith('/submit') and not self.lost:
                self.lost = True
                response.close()
                raise requests.ConnectionError('Submission response lost')
            return response
    with transfer.live(app) as url:
        LostAck(url, timeline='audio', poll_interval=.01, retry_delay=.01).lip_sync(
            *map(str, media), str(tmp_path / 'result.mp4'))
        assert len(app.state.service.jobs) == 1


def test_audio_checkpoint_survives_server_restart(tmp_path, media, transport):
    output = tmp_path / 'result.mp4'
    first_app = create_app(engine=TimelineFixture(), storage=tmp_path / 'server')
    # Reuse the existing ASGI/TCP transport's restart URL translation fixture.
    with transfer.live(first_app) as url:
        first = transfer.client(url, timeline='audio')
        first._download = lambda *args: (_ for _ in ()).throw(TimeoutError('Interrupted'))
        with pytest.raises(TimeoutError):
            first.lip_sync(*map(str, media), str(output))
    second_app = create_app(engine=TimelineFixture(), storage=tmp_path / 'server')
    with transfer.live(second_app) as new_url:
        # TCP test ports differ; keep the recorded identity address consistent
        # to exercise server-ID invalidation rather than changed-address reset.
        checkpoint = output.with_name(output.name + '.lipsync.json')
        state = json.loads(checkpoint.read_text())
        state['identity']['base_url'] = new_url
        checkpoint.write_text(json.dumps(state))
        transfer.client(new_url, timeline='audio').lip_sync(*map(str, media), str(output))
        assert len(second_app.state.service.jobs) == 1


def test_job_mode_and_duration_mismatch_are_not_published(tmp_path, media, transport):
    app = create_app(engine=TimelineFixture(), storage=tmp_path / 'server')
    class WrongMetadata(RemoteLipSync):
        def _api(self, method, path, **kwargs):
            job = super()._api(method, path, **kwargs)
            if path.startswith('jobs/'):
                job['timeline'] = 'strict'
            return job
    with transfer.live(app) as url:
        output = tmp_path / 'result.mp4'
        with pytest.raises(RuntimeError, match='different timeline'):
            WrongMetadata(url, timeline='audio', poll_interval=.01).lip_sync(*map(str, media), str(output))
        assert not output.exists()


@pytest.mark.parametrize('failure_kind', ['validation', 'inference'])
def test_failed_audio_job_is_not_automatically_resubmitted(tmp_path, media, transport, failure_kind):
    from lipsync_service.errors import InferenceError
    failure = (ValueError('Invalid face input') if failure_kind == 'validation' else
               InferenceError('MuseTalk inference failed: ValueError: Invalid face input; frame 42'))
    class Failed(TimelineFixture):
        def run_timeline(self, *args, **kwargs):
            raise failure
    app = create_app(engine=Failed(), storage=tmp_path / 'server')
    with transfer.live(app) as url:
        for _ in range(2):
            with pytest.raises(RuntimeError, match='Invalid face input'):
                transfer.client(url, timeline='audio').lip_sync(*map(str, media), str(tmp_path / 'result.mp4'))
        assert len(app.state.service.jobs) == 1


def test_publication_failure_retains_checked_download_and_resumes_without_new_job(tmp_path, media, transport,
                                                                               monkeypatch):
    import pipeline.lip_sync.remote as remote_module
    app = create_app(engine=TimelineFixture(), storage=tmp_path / 'server')
    output = tmp_path / 'result.mp4'
    publish = remote_module.publish_result
    def full_disk(*args):
        raise OSError(errno.ENOSPC, 'Test disk full')
    with transfer.live(app) as url:
        monkeypatch.setattr(remote_module, 'publish_result', full_disk)
        with pytest.raises(OSError) as exc:
            transfer.client(url, timeline='audio').lip_sync(*map(str, media), str(output))
        assert exc.value.errno == errno.ENOSPC and not output.exists()
        assert output.with_name(output.name + '.lipsync.part').stat().st_size > 0
        assert output.with_name(output.name + '.lipsync.json').exists()
        monkeypatch.setattr(remote_module, 'publish_result', publish)
        transfer.client(url, timeline='audio').lip_sync(*map(str, media), str(output))
        assert len(app.state.service.jobs) == 1 and output.exists()


@pytest.mark.parametrize('timeline', ['strict', 'audio'])
def test_remote_verification_cli_over_real_tcp(tmp_path, media, timeline):
    transfer.TRANSPORT = 'tcp'
    fixture = TimelineFixture()
    output = tmp_path / 'cli.mp4'
    audio = make_audio(tmp_path / 'long.wav', 2) if timeline == 'audio' else media[1]
    server = create_app(engine=fixture, storage=tmp_path / 'server', api_key='test-key')
    client_root = Path(__file__).resolve().parents[2] / 'VideoPrinterTurbo/server'
    with transfer.live(server) as url:
        result = subprocess.run([sys.executable, '-m', 'pipeline.lip_sync.check_remote', '--base-url', url,
                                 '--video', str(media[0]), '--audio', str(audio), '--output', str(output),
                                 '--timeline', timeline], cwd=client_root,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', LIPSYNC_API_KEY='test-key'),
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        report = output.with_suffix('.remote-verification.json').read_text()
        metadata = json.loads(report)
        assert metadata['timeline'] == timeline and metadata['job_id']
        assert metadata['video_duration'] == pytest.approx(2 if timeline == 'audio' else 1, abs=.05)
        assert 'test-key' not in report and fixture.seen == [timeline]
