"""Both implementations via ASGI and TCP transports; fixture does no mouth animation."""
import hashlib
import io
import json
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
import requests
import uvicorn
from fastapi.testclient import TestClient

from app import create_app
from lipsync_service.engine import CommandEngine, digest
from pipeline.lip_sync.remote import RemoteLipSync


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp('media')
    video, audio = root / 'source.mp4', root / 'voice.wav'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=160x120:rate=25:duration=1', '-c:v', 'libx264', str(video)], check=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=1', str(audio)], check=True)
    return video, audio


def payload(media):
    return {kind: {'name': path.name, 'size': path.stat().st_size, 'sha256': digest(path)}
            for kind, path in zip(('video', 'audio'), media)}


def engine():
    # Command protocol fixture only: deliberately copies original frames.
    return CommandEngine(['ffmpeg', '-nostdin', '-v', 'error', '-i', '{video}', '-i', '{audio}',
                          '-map', '0:v:0', '-c:v', 'copy', '{output}'], timeout=30)


TRANSPORT = 'asgi'


@pytest.fixture(params=['asgi', 'tcp'])
def transport(request):
    global TRANSPORT
    TRANSPORT = request.param


@contextmanager
def live(app):
    if TRANSPORT == 'asgi':
        with TestClient(app) as asgi:
            def send(session, prepared, **kwargs):
                reply = asgi.request(prepared.method, prepared.url, headers=dict(prepared.headers),
                                     content=prepared.body, follow_redirects=False)
                result = requests.Response()
                result.status_code = reply.status_code
                result.headers = requests.structures.CaseInsensitiveDict(reply.headers)
                result.raw = io.BytesIO(reply.content)
                result.url = prepared.url
                result.request = prepared
                return result
            with patch.object(requests.sessions.Session, 'send', send):
                yield 'http://asgi.test'
        return
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', 0))
    except PermissionError:
        sock.close()
        pytest.skip('This environment prohibits TCP listener binding; run in an ordinary terminal')
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError('Test server failed to start')
        time.sleep(.01)
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(timeout=40)
        sock.close()
        assert not thread.is_alive()


def client(url, **kwargs):
    return RemoteLipSync(url, poll_interval=.01, retry_delay=.01, request_timeout=5,
                         chunk_size=4096, **kwargs)


def upload_all(c, media):
    uid = c.post('/v1/uploads', json=payload(media)).json()['data']['upload_id']
    for kind, path in zip(('video', 'audio'), media):
        assert c.patch(f'/v1/uploads/{uid}/{kind}', content=path.read_bytes(),
                       headers={'Upload-Offset': '0'}).status_code == 200
    return uid


def wait(c, jid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = c.get('/v1/jobs/' + jid).json()['data']
        if result['status'] in ('succeeded', 'failed'):
            return result
        time.sleep(.01)
    pytest.fail('Job did not finish')


def test_upload_offsets_checksum_limits_auth(tmp_path, media):
    with TestClient(create_app(engine=engine(), storage=tmp_path, api_key='test', chunk_size=4096)) as c:
        assert c.post('/v1/uploads', json=payload(media)).status_code == 401
        c.headers['Authorization'] = 'Bearer test'
        uid = c.post('/v1/uploads', json=payload(media)).json()['data']['upload_id']
        path = f'/v1/uploads/{uid}/video'
        assert c.patch(path, content=b'abc', headers={'Upload-Offset': '1'}).status_code == 409
        assert c.patch(path, content=b'x' * 4097, headers={'Upload-Offset': '0'}).status_code == 413
        assert c.patch(path, content=b'abc', headers={'Upload-Offset': '0'}).status_code == 200
        assert c.get(f'/v1/uploads/{uid}').json()['data']['files']['video']['offset'] == 3
        assert c.post(f'/v1/uploads/{uid}/submit').status_code == 409
        spec = payload(media)
        spec['video']['name'] = '../video.mp4'
        assert c.post('/v1/uploads', json=spec).status_code == 422
    with TestClient(create_app(engine=engine(), storage=tmp_path, max_file=1)) as c:
        assert c.post('/v1/uploads', json=payload(media)).status_code == 413
    with TestClient(create_app(engine=engine(), storage=tmp_path)) as c:
        spec = payload(media)
        spec['video']['sha256'] = '0' * 64
        uid = c.post('/v1/uploads', json=spec).json()['data']['upload_id']
        for kind, path in zip(('video', 'audio'), media):
            c.patch(f'/v1/uploads/{uid}/{kind}', content=path.read_bytes(), headers={'Upload-Offset': '0'})
        assert c.post(f'/v1/uploads/{uid}/submit').status_code == 422


def test_job_idempotency_range_and_cleanup(tmp_path, media):
    with TestClient(create_app(engine=engine(), storage=tmp_path, ttl=60)) as c:
        uid = upload_all(c, media)
        job = c.post(f'/v1/uploads/{uid}/submit').json()['data']
        assert c.post(f'/v1/uploads/{uid}/submit').json()['data']['job_id'] == job['job_id']
        assert c.patch(f'/v1/uploads/{uid}/video', content=b'x', headers={'Upload-Offset': '0'}).status_code == 409
        done = wait(c, job['job_id'])
        assert done['status'] == 'succeeded', done
        url = f"/v1/jobs/{job['job_id']}/result"
        full = c.get(url)
        tail = c.get(url, headers={'Range': 'bytes=10-', 'If-Range': full.headers['etag']})
        assert tail.status_code == 206
        assert tail.content == full.content[10:]
        assert digest_bytes(full.content) == done['result']['sha256']
        assert c.get(url, headers={'Range': 'bytes=99999999-'}).status_code == 416
        assert c.get(url, headers={'Range': 'bytes=10-', 'If-Range': '"wrong"'}).status_code == 200
        service = c.app.state.service
        service.uploads[uid].touched -= 61
        service.cleanup()
        assert c.get(f'/v1/uploads/{uid}').status_code == 404
        assert c.get(f"/v1/jobs/{job['job_id']}").status_code == 404


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def test_end_to_end(tmp_path, media, transport):
    with live(create_app(engine=engine(), storage=tmp_path / 'server', api_key='secret')) as url:
        output = tmp_path / 'out.mp4'
        remote = client(url, api_key='secret')
        assert remote.lip_sync(*map(str, media), str(output)) == str(output)
        assert output.stat().st_size > 0
        assert not output.with_name('out.mp4.lipsync.json').exists()
        with pytest.raises(ValueError):
            remote.lip_sync(*map(str, media), str(output))
        assert all(p.exists() for p in media)


def test_lost_patch_and_submit_ack(tmp_path, media, transport):
    class LostAck(RemoteLipSync):
        lost_patch = False
        lost_submit = False

        def _request(self, method, path, **kwargs):
            response = super()._request(method, path, **kwargs)
            if method == 'PATCH' and not self.lost_patch:
                self.lost_patch = True
                response.close()
                raise requests.ConnectionError('Injected response loss after PATCH committed')
            if path.endswith('/submit') and not self.lost_submit:
                self.lost_submit = True
                response.close()
                raise requests.ConnectionError('Injected response loss after submit committed')
            return response
    app = create_app(engine=engine(), storage=tmp_path / 'server')
    with live(app) as url:
        remote = LostAck(url, chunk_size=4096, poll_interval=.01, retry_delay=.01)
        output = tmp_path / 'ack.mp4'
        remote.lip_sync(*map(str, media), str(output))
        assert remote.lost_patch and remote.lost_submit
        assert len(app.state.service.jobs) == 1
        assert len(app.state.service.uploads) == 1


def test_process_resume_upload_and_server_restart(tmp_path, media, transport):
    class StopUpload(RemoteLipSync):
        def _request(self, method, path, **kwargs):
            response = super()._request(method, path, **kwargs)
            if method == 'PATCH':
                response.close()
                raise TimeoutError('Simulated process interruption')
            return response
    storage = tmp_path / 'server'
    output = tmp_path / 'resume.mp4'
    with live(create_app(engine=engine(), storage=storage)) as url:
        with pytest.raises(TimeoutError):
            StopUpload(url, chunk_size=4096).lip_sync(*map(str, media), str(output))
        checkpoint = output.with_name(output.name + '.lipsync.json')
        state = json.loads(checkpoint.read_text())
        session = requests.get(url + '/v1/uploads/' + state['upload_id']).json()['data']
        assert session['files']['video']['offset'] == 4096
        client(url).lip_sync(*map(str, media), str(output))
        # New output checkpoint points to an ID that the next server cannot know.
        stale = tmp_path / 'stale.mp4'
        state['identity']['base_url'] = 'placeholder'
    with live(create_app(engine=engine(), storage=storage)) as url:
        state['identity']['base_url'] = url
        stale.with_name(stale.name + '.lipsync.json').write_text(json.dumps(state))
        client(url).lip_sync(*map(str, media), str(stale))
        assert stale.is_file()


def test_download_checkpoint_resume(tmp_path, media, transport):
    class StopDownload(RemoteLipSync):
        def _download(self, jid, result, part):
            with self._request('GET', f'jobs/{jid}/result', stream=True) as response:
                with part.open('wb') as target:
                    target.write(next(response.iter_content(512)))
            raise TimeoutError('Simulated process interruption during download')

    class ObserveRange(RemoteLipSync):
        ranged = False

        def _request(self, method, path, **kwargs):
            if path.endswith('/result'):
                assert kwargs['headers']['Range'] == 'bytes=512-'
                self.ranged = True
            return super()._request(method, path, **kwargs)
    app = create_app(engine=engine(), storage=tmp_path / 'server')
    with live(app) as url:
        output = tmp_path / 'download.mp4'
        with pytest.raises(TimeoutError):
            StopDownload(url, poll_interval=.01).lip_sync(*map(str, media), str(output))
        remote = ObserveRange(url, poll_interval=.01)
        remote.lip_sync(*map(str, media), str(output))
        assert remote.ranged
        assert len(app.state.service.jobs) == 1


def test_serial_queue_failure_and_expiry(tmp_path, media):
    class BlockingEngine:
        ready = True
        entered = threading.Event()
        release = threading.Event()
        active = 0
        maximum = 0
        calls = 0

        def run(self, video, audio, output):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.calls += 1
            self.entered.set()
            self.release.wait(5)
            self.active -= 1
            raise ValueError('Test model failure')
    blocking = BlockingEngine()
    with TestClient(create_app(engine=blocking, storage=tmp_path, ttl=60, capacity=2)) as c:
        uid1, uid2 = upload_all(c, media), upload_all(c, media)
        j1 = c.post(f'/v1/uploads/{uid1}/submit').json()['data']['job_id']
        assert blocking.entered.wait(2)
        j2 = c.post(f'/v1/uploads/{uid2}/submit').json()['data']['job_id']
        assert c.get('/v1/jobs/' + j2).json()['data']['status'] == 'queued'
        assert c.get('/v1/jobs/' + j1 + '/result').status_code == 409
        service = c.app.state.service
        for u in service.uploads.values():
            u.touched -= 120
        service.cleanup()
        assert len(service.uploads) == 2  # queued/running never expire
        assert c.post('/v1/uploads', json=payload(media)).status_code == 429
        blocking.release.set()
        assert wait(c, j1)['status'] == 'failed'
        assert wait(c, j2)['error'] == 'Test model failure'
        assert blocking.maximum == 1
        assert c.post(f'/v1/uploads/{uid1}/submit').json()['data']['job_id'] == j1
        assert blocking.calls == 2


def test_unconfigured_engine_and_remote_failure(tmp_path, media, transport):
    with TestClient(create_app(engine=CommandEngine([], 1), storage=tmp_path)) as c:
        uid = upload_all(c, media)
        assert c.post(f'/v1/uploads/{uid}/submit').status_code == 503
    class FailEngine:
        ready = True
        def run(self, *args):
            raise ValueError('Invalid model input')
    with live(create_app(engine=FailEngine(), storage=tmp_path)) as url:
        remote = client(url)
        with pytest.raises(RuntimeError, match='Invalid model input'):
            remote.lip_sync(*map(str, media), str(tmp_path / 'fail.mp4'))
        assert remote.last_job_id
        assert not (tmp_path / 'fail.mp4').exists()


def test_corrupt_result_never_published(tmp_path, media, transport):
    class CorruptMetadata(RemoteLipSync):
        def _download(self, jid, result, part):
            return super()._download(jid, {**result, 'sha256': '0' * 64}, part)
    with live(create_app(engine=engine(), storage=tmp_path / 'server')) as url:
        output = tmp_path / 'corrupt.mp4'
        with pytest.raises(requests.ConnectionError, match='corrupt'):
            CorruptMetadata(url, poll_interval=.01, retry_delay=.01, retries=1).lip_sync(
                *map(str, media), str(output))
        assert not output.exists()
        assert output.with_name(output.name + '.lipsync.json').exists()


def test_local_timeout_keeps_job_checkpoint(tmp_path, media, transport):
    class SlowEngine:
        ready = True
        def run(self, video, audio, output):
            time.sleep(.2)
            engine().run(video, audio, output)
    app = create_app(engine=SlowEngine(), storage=tmp_path / 'server')
    with live(app) as url:
        output = tmp_path / 'timeout.mp4'
        with pytest.raises(TimeoutError):
            client(url, job_timeout=.1).lip_sync(*map(str, media), str(output))
        state = json.loads(output.with_name(output.name + '.lipsync.json').read_text())
        assert state['job_id']
        client(url).lip_sync(*map(str, media), str(output))
        assert len(app.state.service.jobs) == 1


def test_no_decoder_or_changed_timeline_is_rejected(tmp_path, media):
    wrong = tmp_path / 'short.wav'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=duration=0.5', str(wrong)], check=True)
    with pytest.raises(ValueError, match='timeline'):
        client('http://127.0.0.1:1').lip_sync(str(media[0]), str(wrong), str(tmp_path / 'out.mp4'))
    # A failing executable does not become a succeeded job or expose its output.
    with TestClient(create_app(engine=CommandEngine(['definitely-not-an-installed-model'], 1),
                               storage=tmp_path / 'server')) as c:
        uid = upload_all(c, media)
        jid = c.post(f'/v1/uploads/{uid}/submit').json()['data']['job_id']
        assert wait(c, jid)['status'] == 'failed'
        assert c.get('/v1/jobs/' + jid + '/result').status_code == 409


def test_single_process_lock_and_abandoned_run_cleanup(tmp_path):
    storage = tmp_path / 'server'
    storage.mkdir()
    abandoned = storage / 'lipsync-abcd1234'
    abandoned.mkdir()
    (abandoned / 'partial.mp4').write_bytes(b'old data')
    with TestClient(create_app(engine=engine(), storage=storage)):
        assert not abandoned.exists()
        with pytest.raises(RuntimeError, match='already in use'):
            with TestClient(create_app(engine=engine(), storage=storage)):
                pass
    with TestClient(create_app(engine=engine(), storage=storage)):
        pass  # lock released on shutdown


def test_model_timeout_is_failure_and_queue_advances(tmp_path, media):
    import sys
    sleepy = CommandEngine([sys.executable, '-c', 'import time; time.sleep(10)'], .05)
    with TestClient(create_app(engine=sleepy, storage=tmp_path)) as c:
        uid = upload_all(c, media)
        jid = c.post(f'/v1/uploads/{uid}/submit').json()['data']['job_id']
        uid2 = upload_all(c, media)
        jid2 = c.post(f'/v1/uploads/{uid2}/submit').json()['data']['job_id']
        assert wait(c, jid)['error'] == 'TimeoutExpired'
        assert wait(c, jid2)['error'] == 'TimeoutExpired'
        assert c.get('/v1/jobs/' + jid + '/result').status_code == 409
