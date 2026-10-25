"""Windows resource tests; Win32 calls are mocked on non-Windows hosts."""
import ctypes
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from lipsync_service import musetalk_runtime, windows_process
from lipsync_service.musetalk_engine import MuseTalkEngine


def test_junction_paths_are_passed_as_environment_values(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(musetalk_runtime.subprocess, 'run', lambda *a, **kw: calls.append((a, kw)))
    link, target = tmp_path / 'link with & spaces', tmp_path / 'target %name%'
    musetalk_runtime._create_junction(link, target)
    command = calls[0][0][0]
    assert command[0] == 'powershell.exe'
    assert str(link) not in command[-1] and str(target) not in command[-1]
    assert calls[0][1]['env']['VPT_JUNCTION_PATH'] == str(link)
    assert calls[0][1]['env']['VPT_JUNCTION_TARGET'] == str(target)
    assert calls[0][1]['check'] is True


def test_python310_junction_detection_uses_reparse_tag():
    junction = SimpleNamespace(is_symlink=lambda: False,
                               lstat=lambda: SimpleNamespace(st_reparse_tag=0xA0000003))
    regular = SimpleNamespace(is_symlink=lambda: False, lstat=lambda: SimpleNamespace())
    assert musetalk_runtime._is_directory_link(junction)
    assert not musetalk_runtime._is_directory_link(regular)


@pytest.mark.parametrize('cross_volume', [False, True])
def test_detector_cache_does_not_require_symlink_privileges(tmp_path, monkeypatch, cross_volume):
    source, cache = tmp_path / 'source.pth', tmp_path / 'cache' / 'detector.pth'
    source.write_bytes(b'detector weights')
    if cross_volume:
        def fail_link(*args):
            raise OSError('Different volumes')
        monkeypatch.setattr(musetalk_runtime.os, 'link', fail_link)
    musetalk_runtime.prepare_detector_cache(source, cache, use_symlink=False)
    assert cache.read_bytes() == source.read_bytes()
    assert not cache.is_symlink()
    musetalk_runtime.prepare_detector_cache(source, cache, use_symlink=False)
    assert cache.read_bytes() == source.read_bytes()


class WinCall:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


@pytest.mark.parametrize('attach_succeeds', [True, False])
def test_job_object_owns_tree_and_closes_handles(monkeypatch, attach_succeeds):
    closed, limits = [], []
    def configure(handle, info_class, pointer, length):
        assert handle == 123 and info_class == 9
        assert length == ctypes.sizeof(windows_process._ExtendedLimits)
        limits.append(ctypes.cast(pointer, ctypes.POINTER(windows_process._ExtendedLimits)).contents.Basic.Flags)
        return True
    def open_process(access, inherit, pid):
        assert access == 0x0101 and not inherit and pid == 789
        return 456
    api = SimpleNamespace(
        CreateJobObjectW=WinCall(lambda security, name: 123),
        SetInformationJobObject=WinCall(configure), OpenProcess=WinCall(open_process),
        AssignProcessToJobObject=WinCall(lambda job, process: attach_succeeds),
        CloseHandle=WinCall(lambda handle: closed.append(handle) or True))
    monkeypatch.setattr(windows_process.ctypes, 'WinDLL', lambda *a, **kw: api, raising=False)
    monkeypatch.setattr(windows_process.ctypes, 'get_last_error', lambda: 5, raising=False)
    monkeypatch.setattr(windows_process.ctypes, 'WinError', lambda code: OSError(code, 'Denied'), raising=False)
    if attach_succeeds:
        job = windows_process.WindowsProcessJob(789)
        assert closed == [456]
        job.close()
        job.close()
    else:
        with pytest.raises(OSError):
            windows_process.WindowsProcessJob(789)
    assert closed == [456, 123]
    assert limits == [0x2000]


def test_engine_closes_job_even_when_worker_has_already_exited():
    calls = []
    engine = MuseTalkEngine()
    engine._windows_job = SimpleNamespace(close=lambda: calls.append('close'))
    engine.close()
    engine.close()
    assert calls == ['close']


def test_real_worker_startup_gate_and_initialization_failure(tmp_path):
    engine = MuseTalkEngine(model_dir=tmp_path / 'missing-models', runtime_dir=tmp_path / 'runtime',
                            startup_timeout=15)
    try:
        with pytest.raises(RuntimeError, match='initialization failed'):
            engine.start()
        assert not engine.ready
        assert 'Missing or corrupt model files' in (tmp_path / 'runtime' / 'worker.log').read_text()
    finally:
        engine.close()
