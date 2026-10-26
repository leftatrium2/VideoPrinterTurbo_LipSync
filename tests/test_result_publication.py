"""Use actual exclusive rename on this host; Windows syscall path is mocked."""
import errno
from types import SimpleNamespace

import pytest

from pipeline.lip_sync import _publish


def unsupported(*args):
    raise OSError(errno.ENOTSUP, 'Hard links unsupported')


def test_unsupported_hardlinks_use_complete_atomic_result(tmp_path, monkeypatch):
    part, output = tmp_path / 'result.part', tmp_path / 'result.mp4'
    part.write_bytes(b'complete checked result')
    monkeypatch.setattr(_publish.os, 'link', unsupported)
    _publish.publish_result(part, output)
    assert output.read_bytes() == b'complete checked result'
    assert not part.exists()


def test_target_created_during_fallback_is_never_overwritten(tmp_path, monkeypatch):
    part, output = tmp_path / 'result.part', tmp_path / 'result.mp4'
    part.write_bytes(b'new result')
    def racing_link(*args):
        output.write_bytes(b'other owner')
        unsupported()
    monkeypatch.setattr(_publish.os, 'link', racing_link)
    with pytest.raises(FileExistsError):
        _publish.publish_result(part, output)
    assert output.read_bytes() == b'other owner' and part.read_bytes() == b'new result'


@pytest.mark.parametrize('code', [errno.EACCES, errno.ENOSPC, errno.EEXIST])
def test_permission_disk_and_existing_file_errors_do_not_fall_back(tmp_path, monkeypatch, code):
    part, output = tmp_path / 'result.part', tmp_path / 'result.mp4'
    part.write_bytes(b'checked')
    def fail(*args):
        raise OSError(code, 'Test publication error')
    monkeypatch.setattr(_publish.os, 'link', fail)
    monkeypatch.setattr(_publish, '_rename_noreplace', lambda *a: pytest.fail('Unsafe error fallback'))
    with pytest.raises(OSError) as exc:
        _publish.publish_result(part, output)
    assert exc.value.errno == code and part.exists() and not output.exists()


def test_windows_branch_uses_non_replacing_rename(tmp_path, monkeypatch):
    part, output = tmp_path / 'result.part', tmp_path / 'result.mp4'
    calls = []
    monkeypatch.setattr(_publish, 'sys', SimpleNamespace(platform='win32'))
    def rename(source, target):
        calls.append((source, target))
        raise FileExistsError('Existing output')
    monkeypatch.setattr(_publish.os, 'rename', rename)
    with pytest.raises(FileExistsError):
        _publish._rename_noreplace(part, output)
    assert calls == [(part, output)]
