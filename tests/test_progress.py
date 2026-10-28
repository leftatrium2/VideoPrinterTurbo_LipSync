"""Self-hosted progress over the real ASGI API and resumable caller."""
import threading

import pytest

from fastapi.testclient import TestClient

import test_transfer as transfer
from app import create_app
from lipsync_service.engine import CommandEngine
from pipeline.lip_sync.remote import RemoteLipSync

media = transfer.media


class ProgressEngine(CommandEngine):
    def __init__(self):
        fixture = transfer.engine()
        super().__init__(fixture.command, fixture.timeout)
        self.on_progress = None
        self.advanced, self.release = threading.Event(), threading.Event()

    def run(self, video, audio, output):
        self.on_progress({'stage': 'inference', 'completed': 25, 'total': 100, 'unit': 'frames'})
        self.advanced.set()
        if not self.release.wait(5):
            raise TimeoutError('Test did not release inference')
        super().run(video, audio, output)


def test_job_progress_and_live_queue_position_are_available(tmp_path, media):
    engine = ProgressEngine()
    app = create_app(engine=engine, storage=tmp_path / 'storage')
    with TestClient(app) as client:
        try:
            first = transfer.upload_all(client, media)
            jid = client.post(f'/v1/uploads/{first}/submit').json()['data']['job_id']
            assert engine.advanced.wait(3)
            job = client.get(f'/v1/jobs/{jid}').json()['data']
            assert job['status'] == 'running'
            assert job['progress'] == {'stage': 'inference', 'percent': 25., 'completed': 25,
                                       'total': 100, 'unit': 'frames'}
            second = transfer.upload_all(client, media)
            queued = client.post(f'/v1/uploads/{second}/submit').json()['data']
            assert queued['progress']['queue_position'] == 1
            assert queued['progress']['percent'] is None
            # A snapshot cannot mutate the service's stored progress.
            snapshot = app.state.service.job(jid)
            snapshot['progress']['percent'] = 90
            assert app.state.service.job(jid)['progress']['percent'] == 25
        finally:
            engine.release.set()
        assert transfer.wait(client, jid)['progress'] == {'stage': 'completed', 'percent': 100}
    assert engine.on_progress is None


@pytest.mark.parametrize('legacy', [False, True])
def test_caller_receives_frame_progress_and_legacy_jobs_still_work(tmp_path, media, monkeypatch, legacy):
    monkeypatch.setattr(transfer, 'TRANSPORT', 'asgi')
    engine = ProgressEngine()
    app = create_app(engine=engine, storage=tmp_path / 'storage')
    events = []
    class Observer(RemoteLipSync):
        def _api(self, method, path, **kwargs):
            result = super()._api(method, path, **kwargs)
            if path.startswith('jobs/') and result.get('progress', {}).get('percent') == 25:
                engine.release.set()
            if legacy and path.startswith('jobs/'):
                result.pop('progress', None)
            return result
    with transfer.live(app) as url:
        try:
            output = tmp_path / 'result.mp4'
            client = Observer(url, poll_interval=.01, on_progress=events.append)
            client.lip_sync(*map(str, media), str(output))
        finally:
            engine.release.set()
    if legacy:
        assert any(event['stage'] == 'running' and event['percent'] is None for event in events)
    else:
        assert any(event['stage'] == 'inference' and event['percent'] == 25 for event in events)
    assert events[-1]['stage'] == 'completed'
    assert events[-1]['percent'] == 100
    stages = [event['stage'] for event in events]
    assert stages.index('completed') > stages.index('download')
    assert output.is_file()


def test_worker_progress_callback_failure_does_not_abort_inference():
    from unittest.mock import Mock
    from lipsync_service.musetalk_engine import MuseTalkEngine
    engine = MuseTalkEngine()
    engine._connection = Mock()
    engine._connection.poll.return_value = True
    engine._connection.recv.side_effect = [('progress', {'stage': 'inference', 'completed': 1}), ('done', None)]
    engine.on_progress = Mock(side_effect=RuntimeError('UI unavailable'))
    assert engine._receive(1, idle_timeout=1) == ('done', None)
    engine.on_progress.assert_called_once()
