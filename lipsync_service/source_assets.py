"""Check bundled source independently of CUDA, dependencies and model weights."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_source(source_dir=None, manifest_path=None):
    source = Path(source_dir or ROOT / 'third_party' / 'MuseTalk')
    manifest = Path(manifest_path or ROOT / 'third_party' / 'MuseTalk.source.json')
    if not manifest.is_file():
        raise RuntimeError('MuseTalk source manifest is missing; re-sync the complete project.')
    expected = json.loads(manifest.read_text(encoding='utf-8'))['files']
    missing, changed = [], []
    for name, checksum in expected.items():
        path = source / name
        if not path.is_file():
            missing.append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            changed.append(name)
    if missing or changed:
        details = []
        if missing:
            details.append('missing: ' + ', '.join(missing))
        if changed:
            details.append('modified: ' + ', '.join(changed))
        raise RuntimeError('MuseTalk bundled source is incomplete or changed (' + '; '.join(details) +
                           '). Re-sync third_party/MuseTalk from the same project revision. '
                           'Downloading model weights does not restore Python source files.')
    return len(expected)


if __name__ == '__main__':
    print(f'MuseTalk bundled source verified ({verify_source()} files).')
