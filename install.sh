#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'Supported deployment target: Ubuntu x86_64 with an NVIDIA GPU.' >&2
  exit 1
fi
if [[ ! -f /etc/os-release ]] || ! grep -q '^ID=ubuntu$' /etc/os-release; then
  echo 'This installer currently supports Ubuntu only.' >&2
  exit 1
fi
command -v nvidia-smi >/dev/null || { echo 'Install the NVIDIA driver first (nvidia-smi is missing).' >&2; exit 1; }
nvidia-smi
command -v conda >/dev/null || { echo 'Install Miniconda/Conda first, then rerun bash install.sh.' >&2; exit 1; }

# System dependencies may require the user's sudo password on a fresh machine.
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null || \
   ! command -v gcc >/dev/null || ! python3 -c \
   'import ctypes; ctypes.CDLL("libGL.so.1"); ctypes.CDLL("libglib-2.0.so.0")' 2>/dev/null; then
  if [[ "$EUID" == 0 ]]; then
    apt-get update
    apt-get install -y ffmpeg libgl1 libglib2.0-0 build-essential
  else
    sudo apt-get update
    sudo apt-get install -y ffmpeg libgl1 libglib2.0-0 build-essential
  fi
fi

# The deployment environment is distinct from an existing development .venv.
export CONDA_PKGS_DIRS="$ROOT/.cache/conda"
export PIP_CACHE_DIR="$ROOT/.cache/pip"
export HF_HOME="$ROOT/.cache/huggingface"
export TORCH_HOME="$ROOT/.cache/torch"
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$ROOT/.runtime/tmp"
mkdir -p "$TMPDIR" "$ROOT/deployment-records"
PREFIX="$ROOT/.conda/musetalk"
if [[ ! -x "$PREFIX/bin/python" ]]; then
  conda create --prefix "$PREFIX" python=3.10 -y
fi
PYTHON="$PREFIX/bin/python"
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 10), "Deployment requires Python 3.10"'
"$PYTHON" -m pip install 'pip==24.0' 'setuptools==69.5.1' 'wheel==0.43.0'
"$PYTHON" -m pip install -c deployment/constraints.txt \
  'torch==2.0.1' 'torchvision==0.15.2' 'torchaudio==2.0.2' \
  --index-url https://download.pytorch.org/whl/cu118
"$PYTHON" -m pip install -c deployment/constraints.txt 'numpy==1.23.5' 'scipy==1.10.1'
# Fail clearly if no binary wheel exists, rather than unexpectedly building MMCV.
"$PYTHON" -m pip install -c deployment/constraints.txt 'mmcv==2.0.1' --only-binary=mmcv \
  -f https://download.openmmlab.com/mmcv/dist/cu118/torch2.0/index.html
"$PYTHON" -m pip install --no-build-isolation -c deployment/constraints.txt \
  -r deployment/requirements-inference.txt
"$PYTHON" -m pip check
"$PYTHON" scripts/check_environment.py
"$PYTHON" -m lipsync_service.model_assets
"$PYTHON" scripts/check_environment.py --load-model
"$PYTHON" -m pip freeze > deployment-records/requirements.installed.txt
conda env export --prefix "$PREFIX" > deployment-records/environment.installed.yml
cp third_party/MuseTalk.source.json deployment-records/MuseTalk.source.json
echo 'Installation and model initialization completed. Start with: bash start.sh'
