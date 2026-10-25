"""Pinned model downloads and integrity checks; uses only the Python stdlib."""
import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'deployment' / 'models.json'


def assets():
    return json.loads(MANIFEST.read_text())['assets']


def check_asset(path, spec):
    if not path.is_file() or not path.stat().st_size:
        return False
    size = path.stat().st_size
    if 'size' in spec and size != spec['size']:
        return False
    if 'git_blob_sha1' in spec:
        hasher = hashlib.sha1(f'blob {size}\0'.encode())
    else:
        hasher = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(chunk)
    expected = spec.get('git_blob_sha1') or spec.get('sha256') or spec['sha256_prefix']
    return hasher.hexdigest().startswith(expected)


def verify_models(model_dir):
    invalid = [spec['path'] for spec in assets() if not check_asset(model_dir / spec['path'], spec)]
    if invalid:
        raise RuntimeError('Missing or corrupt model files: ' + ', '.join(invalid) +
                           '. Run bash install.sh to download/repair weights.')


def download_asset(model_dir, spec, endpoint):
    destination = model_dir / spec['path']
    if check_asset(destination, spec):
        print('Verified:', spec['path'], flush=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    url = spec.get('url') or (f"{endpoint.rstrip('/')}/{spec['repo']}/resolve/"
                              f"{spec['revision']}/{spec['file']}")
    for attempt in range(3):
        try:
            if check_asset(partial, spec):
                partial.replace(destination)
                return
            offset = partial.stat().st_size if partial.exists() else 0
            # A complete but invalid download must be replaced, not resumed.
            if spec.get('size') is not None and offset >= spec['size']:
                partial.unlink()
                offset = 0
            request = urllib.request.Request(url, headers={'User-Agent': 'VideoPrinterTurbo-LipSync/1.0'})
            if offset:
                request.add_header('Range', f'bytes={offset}-')
            print('Downloading:', spec['path'], f'(resume from {offset} bytes)', flush=True)
            with urllib.request.urlopen(request, timeout=60) as response:
                append = response.status == 206 and response.headers.get('Content-Range', '').startswith(
                    f'bytes {offset}-')
                if response.status == 206 and not append:
                    raise RuntimeError('Unexpected download range')
                with partial.open('ab' if append else 'wb') as target:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
            if spec.get('size') is not None and partial.stat().st_size < spec['size']:
                raise OSError('Incomplete model download; retained partial file for resume')
            if not check_asset(partial, spec):
                partial.unlink(missing_ok=True)
                raise RuntimeError('Model checksum mismatch: ' + spec['path'])
            partial.replace(destination)
            return
        except (OSError, urllib.error.URLError, RuntimeError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 416:
                partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path,
                        default=Path(os.environ.get('LIPSYNC_MODEL_DIR', ROOT / 'models')))
    parser.add_argument('--check', action='store_true', help='Verify only; no downloads')
    args = parser.parse_args()
    if not args.check:
        for spec in assets():
            download_asset(args.model_dir, spec, os.environ.get('HF_ENDPOINT', 'https://huggingface.co'))
    verify_models(args.model_dir)
    print('All MuseTalk v15 inference weights verified.')


if __name__ == '__main__':
    main()
