#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.conda/musetalk/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo 'Run bash install.sh first; the deployment environment is missing.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
export LIPSYNC_ENGINE=musetalk
# Avoid accidentally selecting the legacy command backend in an old shell.
unset LIPSYNC_COMMAND
# Performance defaults; explicit environment settings take precedence.
export LIPSYNC_BATCH_SIZE="${LIPSYNC_BATCH_SIZE:-2}"
export LIPSYNC_FRAME_MEMORY_MB="${LIPSYNC_FRAME_MEMORY_MB:-512}"
export LIPSYNC_FLOAT16="${LIPSYNC_FLOAT16:-1}"
export LIPSYNC_DETECT_MAX_SIDE="${LIPSYNC_DETECT_MAX_SIDE:-1280}"
export LIPSYNC_PROFILE="${LIPSYNC_PROFILE:-1}"
export LIPSYNC_STORAGE="${LIPSYNC_STORAGE:-$ROOT/storage}"
exec "$PYTHON" -m uvicorn app:app --host "${LIPSYNC_HOST:-0.0.0.0}" \
  --port "${LIPSYNC_PORT:-8081}" --workers 1 --timeout-graceful-shutdown 4200
