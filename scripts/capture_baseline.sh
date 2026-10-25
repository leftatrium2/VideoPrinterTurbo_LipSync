#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="${1:-$HOME/apps/MuseTalk}"
RECORDS="$ROOT/deployment-records/server-baseline"
mkdir -p "$RECORDS"
git -C "$SOURCE" rev-parse HEAD > "$RECORDS/musetalk.commit.txt"
python -m pip freeze > "$RECORDS/requirements.working.txt"
conda env export > "$RECORDS/environment.working.yml"
nvidia-smi > "$RECORDS/nvidia-smi.txt"
python - "$SOURCE" "$RECORDS/models.sha256.json" <<'PY'
import hashlib, json, pathlib, sys
source = pathlib.Path(sys.argv[1]) / 'models'
result = {}
for path in sorted(source.rglob('*')):
    if not path.is_file() or '.cache' in path.parts:
        continue
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    result[path.relative_to(source).as_posix()] = {'size': path.stat().st_size, 'sha256': digest.hexdigest()}
pathlib.Path(sys.argv[2]).write_text(json.dumps(result, indent=2) + '\n')
PY
echo "Baseline saved to $RECORDS (run this script inside the working musetalk Conda environment)."
