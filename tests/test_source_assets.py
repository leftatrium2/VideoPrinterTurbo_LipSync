"""Catch source files silently excluded from deployment by Git ignore rules."""
import json
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from lipsync_service.source_assets import verify_source

ROOT = Path(__file__).resolve().parents[1]


def test_vendored_manifest_files_are_not_ignored_by_git():
    if not shutil.which('git') or not (ROOT / '.git').exists():
        pytest.skip('Git ignore regression requires a Git checkout')
    manifest = json.loads((ROOT / 'third_party/MuseTalk.source.json').read_text())
    paths = ['third_party/MuseTalk/' + name for name in manifest['files']]
    result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin'],
                            cwd=ROOT, input='\n'.join(paths) + '\n', capture_output=True, text=True)
    assert result.returncode in (0, 1), result.stderr
    assert not result.stdout.strip(), 'Vendored source is omitted from Git deployment:\n' + result.stdout


def test_root_model_weights_remain_ignored():
    if not shutil.which('git') or not (ROOT / '.git').exists():
        pytest.skip('Git ignore regression requires a Git checkout')
    result = subprocess.run(['git', 'check-ignore', '--no-index', 'models/musetalkV15/unet.pth'],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_source_preflight_catches_missing_model_python_files_without_cuda(tmp_path):
    source = tmp_path / 'MuseTalk'
    source.mkdir()
    path = 'musetalk/models/vae.py'
    content = b'class VAE: pass\n'
    manifest = tmp_path / 'source.json'
    manifest.write_text(json.dumps({'files': {path: hashlib.sha256(content).hexdigest()}}))
    with pytest.raises(RuntimeError, match=r'missing: musetalk/models/vae.py'):
        verify_source(source, manifest)
    target = source / path
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    assert verify_source(source, manifest) == 1
    target.write_bytes(b'corrupt')
    with pytest.raises(RuntimeError, match=r'modified: musetalk/models/vae.py'):
        verify_source(source, manifest)
