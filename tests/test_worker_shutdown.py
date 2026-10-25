"""Exercise semaphore cleanup through a complete Python interpreter shutdown."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def ignore_shutdown_worker(connection, settings):
    if os.name == 'posix':
        os.setsid()
    connection.send(('ready', None))
    connection.recv()
    time.sleep(60)


def test_unresponsive_shutdown_still_recycles_process_tree(tmp_path, monkeypatch, caplog):
    from lipsync_service import musetalk_engine
    monkeypatch.setattr(musetalk_engine, 'SHUTDOWN_GRACE_SECONDS', .1)
    engine = musetalk_engine.MuseTalkEngine(worker_target=ignore_shutdown_worker,
                                           runtime_dir=tmp_path / 'worker', startup_timeout=10)
    try:
        engine.start()
        start = time.monotonic()
        engine.close()
        assert time.monotonic() - start < 3
        assert engine._process is None and not engine.ready
        assert 'terminating its process tree' in caplog.text
    finally:
        engine.close()


@pytest.mark.skipif(os.name != 'posix', reason='Named semaphore resource tracking is POSIX-specific')
def test_normal_close_does_not_leak_worker_semaphores(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / 'semaphore_worker.py'
    script.write_text('''import multiprocessing
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lipsync_service.musetalk_engine import MuseTalkEngine

def worker(connection, settings):
    os.setsid()
    semaphore = multiprocessing.get_context('spawn').Semaphore(1)
    connection.send(('ready', None))
    try:
        assert connection.recv() is None
        Path(settings['runtime'], 'normal-exit').touch()
    finally:
        connection.close()

if __name__ == '__main__':
    engine = MuseTalkEngine(worker_target=worker, runtime_dir=sys.argv[2], startup_timeout=10)
    engine.start()
    engine.close()
''', encoding='utf-8')
    result = subprocess.run([sys.executable, '-W', 'default', str(script), str(root), str(tmp_path / 'worker')],
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True,
                            text=True, timeout=25)
    assert result.returncode == 0, result.stderr
    assert 'leaked semaphore' not in result.stderr, result.stderr
    assert (tmp_path / 'worker' / 'normal-exit').exists(), 'Worker was killed before normal cleanup'
