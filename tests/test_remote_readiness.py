"""Deterministic readiness/recovery budgets without waiting for GPU startup."""
import io
import json
from types import SimpleNamespace

import pytest
import requests

from pipeline.lip_sync import remote


class Clock:
    now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def reply(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    response._content_consumed = True
    response.raw = io.BytesIO(response._content)
    return response


def setup_client(monkeypatch, callback, *, timeline='strict', readiness=60, budget=100):
    clock = Clock()
    monkeypatch.setattr(remote, 'time', clock)
    client = remote.RemoteLipSync('http://unit.test:8081', poll_interval=2, retry_delay=1,
                                  timeline=timeline, readiness_timeout=readiness)
    client._deadline = budget
    client._session = SimpleNamespace(request=lambda *a, **kw: callback(clock, *a, **kw))
    return client, clock


def test_submit_waits_for_54_second_recovery_without_repeated_job_creation(monkeypatch):
    submitted, health_calls = [], []
    def request(clock, method, url, **kwargs):
        if url.endswith('/health'):
            health_calls.append(url)
            return reply(503 if clock.now < 54 else 200,
                         {'ready': clock.now >= 54, 'timelines': ['strict', 'audio']})
        submitted.append(url)
        if clock.now < 54:
            return reply(503, {'detail': 'LipSync engine is not ready'})
        return reply(200, {'code': 0, 'data': {'job_id': 'once'}})
    client, clock = setup_client(monkeypatch, request)
    assert client._api('POST', 'uploads/u/submit', json={'timeline': 'strict'}) == {'job_id': 'once'}
    assert len(submitted) == 2 and 54 <= clock.now < 60
    assert all(url == 'http://unit.test:8081/health' for url in health_calls)


@pytest.mark.parametrize('budget,readiness,message', [(100, 10, 'readiness'), (5, 10, 'LipSync timed out')])
def test_readiness_and_total_budget_are_both_bounded(monkeypatch, budget, readiness, message):
    client, clock = setup_client(monkeypatch, lambda *a, **kw: reply(503, {'detail': 'not ready'}),
                                 budget=budget, readiness=readiness)
    with pytest.raises(TimeoutError, match=message):
        client._wait_ready()
    assert clock.now == min(budget, readiness)


def test_permanent_api_503_is_bounded_even_when_health_says_ready(monkeypatch):
    def request(clock, method, url, **kwargs):
        return reply(200, {'ready': True}) if url.endswith('/health') else reply(503, {'detail': 'gateway unavailable'})
    client, clock = setup_client(monkeypatch, request, readiness=10)
    with pytest.raises(TimeoutError, match='readiness'):
        client._api('POST', 'uploads/u/submit')
    assert clock.now == 10


@pytest.mark.parametrize('status', [401, 403, 422])
def test_auth_and_input_errors_are_not_readiness_retries(monkeypatch, status):
    calls = []
    def request(*args, **kwargs):
        calls.append(1)
        return reply(status, {'detail': 'rejected'})
    client, clock = setup_client(monkeypatch, request)
    with pytest.raises(remote.RemoteHTTPError) as exc:
        client._api('POST', 'uploads/u/submit')
    assert exc.value.status == status and len(calls) == 1 and clock.now == 0


def test_audio_mode_requires_server_capability_confirmation(monkeypatch):
    client, _ = setup_client(monkeypatch, lambda *a, **kw: reply(200, {'ready': True}), timeline='audio')
    with pytest.raises(RuntimeError, match='does not support'):
        client._wait_ready()


def test_legacy_strict_health_and_missing_health_are_supported(monkeypatch):
    client, _ = setup_client(monkeypatch, lambda *a, **kw: reply(200, {'ready': True}))
    client._wait_ready()
    client._session = SimpleNamespace(request=lambda *a, **kw: reply(404, {'detail': 'missing'}))
    client._wait_ready()


def test_successful_http_with_invalid_health_json_fails_fast(monkeypatch):
    def request(*args, **kwargs):
        response = reply(200, {})
        response._content = b'<html>wrong service</html>'
        return response
    client, clock = setup_client(monkeypatch, request)
    with pytest.raises(RuntimeError, match='health JSON'):
        client._wait_ready()
    assert clock.now == 0


@pytest.mark.parametrize('metadata', [{'timeline': 'strict'}, {'timeline': 'audio', 'expected_duration': 9},
                                    {'timeline': 'audio', 'expected_duration': None},
                                    {'timeline': 'audio', 'expected_duration': float('nan')}])
def test_mismatched_audio_job_metadata_is_rejected(metadata):
    client = remote.RemoteLipSync('http://unit.test', timeline='audio')
    with pytest.raises(RuntimeError):
        client._validate_job({'status': 'succeeded', **metadata}, 1)


def test_unlimited_client_can_poll_after_two_hours_with_bounded_requests(monkeypatch):
    client, clock = setup_client(monkeypatch, lambda *a, **kw: reply(200, {}))
    assert client.job_timeout == 0
    client._deadline = float('inf')
    clock.now = 24 * 3600
    timeouts = []
    client._session.request = lambda *a, **kw: (timeouts.append(kw['timeout']) or reply(200, {}))
    client._pause(2)
    with client._request('GET', 'jobs/existing'):
        pass
    assert timeouts == [client.request_timeout]
    assert clock.now == 24 * 3600 + 2
    client._session.request = lambda *a, **kw: reply(503, {'detail': 'not ready'})
    with pytest.raises(TimeoutError, match='readiness'):
        client._wait_ready()
    assert clock.now == 24 * 3600 + 2 + client.readiness_timeout


def test_explicit_deadline_reports_job_and_recovery_instructions(monkeypatch):
    client, clock = setup_client(monkeypatch, lambda *a, **kw: reply(200, {}), budget=10)
    client.job_timeout = 10
    client.last_job_id = 'existing-job'
    with pytest.raises(TimeoutError, match='job_id=existing-job.*checkpoint retained.*job_timeout=0'):
        client._pause(11)
    assert clock.now == 10


@pytest.mark.parametrize('budget', [-1, float('inf'), float('nan'), True])
def test_invalid_total_deadline_rejected(budget):
    with pytest.raises(ValueError, match='job_timeout'):
        remote.RemoteLipSync('http://unit.test', job_timeout=budget)
