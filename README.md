# VideoPrinterTurbo_LipSync

English | [简体中文](README_cn.md)

Local lip-sync backend for VideoPrinterTurbo, with vendored MuseTalk 1.5 (`v15`), FastAPI and Uvicorn. Deploy one project; no separate MuseTalk checkout or inference command configuration is required.

Targets: **Ubuntu x86_64 or native Windows 10/11 x64, NVIDIA GPU, Python 3.10**. Windows uses native PowerShell/batch entry points. Local transport and worker lifecycle tests are available. Clean target-system installation, real CUDA inference and visual quality acceptance remain pending. The vendored commit has not yet been compared with the user's successful server environment. See [integration checklist](doc/MuseTalk/INTEGRATION_TODO.md).

## Deploy

### Windows

Prerequisites: 64-bit Windows 10/11 and a working NVIDIA Windows driver (`nvidia-smi.exe`). Extract the project to a short ASCII path such as `D:\VideoPrinterTurbo_LipSync` for Conda/OpenCV path compatibility.

Double-click `install.bat`, wait for installation and model initialization, then double-click `start.bat`. Keep the service window open and visit <http://127.0.0.1:8081/docs>. From PowerShell:

```powershell
.\install.bat
.\start.bat
```

The installer reuses available Conda or downloads a pinned, SHA-256-verified Miniforge into `.tools/miniforge` without registering it as the default Python. It creates `.conda/musetalk` with Python 3.10, installs FFmpeg and VC++ runtime libraries from conda-forge, and installs Windows binary wheels for PyTorch cu118, MMCV and xtcocotools. No compiler toolchain or separate system FFmpeg setup is needed. Failed commands stop installation.

Directory junctions and detector hard links/copies avoid requiring Developer Mode or symlink privileges. A Windows Job Object owns the model process and descendants for cleanup. Batch entry points set execution policy for their PowerShell process only.

Optional settings in the same PowerShell window:

```powershell
$env:HF_ENDPOINT = 'https://hf-mirror.com'
# $env:HTTPS_PROXY = 'http://127.0.0.1:7890'
.\install.bat
$env:LIPSYNC_HOST = '0.0.0.0'
$env:LIPSYNC_API_KEY = 'replace-with-your-own-key'
.\start.bat
```

Direct entry point: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`; use `start.ps1` to launch. Native Windows installation, DLL loading, junctions, Job Objects and real GPU inference still require validation on Windows hardware. macOS checks do not establish those guarantees.

### Ubuntu

Prerequisites: a working NVIDIA driver (`nvidia-smi`) and Conda in PATH. Installing system libraries may require sudo. The installer uses PyTorch cu118 wheels and checks CUDA/MMCV operations; a separate CUDA Toolkit installation is not required for the selected wheels.

```bash
bash install.sh
bash start.sh
```

The installer creates `.conda/musetalk` with Python 3.10, installs PyTorch 2.0.1 and the MMCV 2.0.1 dependency family, downloads/verifies model assets, then checks actual model initialization. It leaves an existing development `.venv` in place. The inference requirements are an integration baseline, not a full lock exported from the working server.

Weights occupy several GB in `models/`, excluded from Git. The manifest includes the v15 UNet, VAE, Whisper, DWPose, BiSeNet, ResNet18 and S3FD. It excludes v1 and training-only SyncNet. Downloads resume and verified files are skipped. Hugging Face repositories are pinned by commit; integrity details and upstream attribution are in [third_party/README.md](third_party/README.md).

Optional mirror/proxy settings must be reachable from the deployment machine:

```bash
export HF_ENDPOINT=https://hf-mirror.com
# export HTTPS_PROXY=http://127.0.0.1:7890
bash install.sh
```

Startup loads the model before accepting HTTP requests. Initialization failure aborts startup; inspect `.runtime/musetalk/worker.log`.

```bash
curl http://127.0.0.1:8081/
curl http://127.0.0.1:8081/health
```

`GET /` retains `{code:0,msg:"success",data:{}}`. `/health` returns `{"ready":true}` with HTTP 200, or `{"ready":false}` with HTTP 503. Interactive API documentation is at `/docs`; see [API.md](API.md) for transfer semantics.

For access from another machine:

```bash
export LIPSYNC_HOST=0.0.0.0
export LIPSYNC_API_KEY='replace-with-your-own-key'
bash start.sh
```

## Inference and recovery

A persistent spawned Python process loads MuseTalk modules directly and reuses weights across serial jobs. The service does not launch the upstream inference CLI. FFmpeg still handles media decoding, encoding and muxing. Use one HTTP worker and one service instance per GPU.

Timeouts and inference failures recycle the model process and its FFmpeg descendants. Failed jobs stay failed and are never silently retried. A supervisor also recovers idle worker crashes; failed recovery retries every 30 seconds. Health and new submissions may return 503 during recovery. Queued jobs retain ordering and may fail if their initialization attempt fails.

Normal shutdown asks the idle model worker to exit and waits up to 10 seconds for Python resource cleanup before terminating its process tree. Failure and inference-timeout recovery still use forced termination.

Inputs must share a start time and differ in duration by no more than 150 ms. Videos must have a constant frame rate and detectable faces. Output preserves frame count, order and fractional frame rate; no video looping, speed changes or truncation are used. Missing faces or variable frame rate cause failure. Output is fully decoded, duration-checked and muxed with the uploaded audio. Visual lip-sync quality requires manual review.

Preprocessing still stores decoded images and reads the complete clip into memory; long videos require sufficient RAM and disk. Jobs have isolated temporary directories and TTL cleanup. Metadata/queue are in memory; restart invalidates old IDs and clears abandoned run files.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `LIPSYNC_HOST` | `127.0.0.1` | Launcher bind address |
| `LIPSYNC_PORT` | `8081` | Launcher port |
| `LIPSYNC_GPU` | `0` | CUDA_VISIBLE_DEVICES, normally one GPU index |
| `LIPSYNC_BATCH_SIZE` | `1` | Inference batch size |
| `LIPSYNC_FLOAT16` | `1` | 1 for FP16, 0 for FP32 |
| `LIPSYNC_MODEL_DIR` | project/models | Model root; prefer an absolute path |
| `LIPSYNC_STARTUP_TIMEOUT` | `600` | Model initialization timeout, seconds |
| `LIPSYNC_INFERENCE_TIMEOUT` | `3600` | Model inference timeout; media checks have separate limits |
| `LIPSYNC_API_KEY` | empty | Bearer key for all `/v1` routes |
| `LIPSYNC_STORAGE` | project/storage | Launcher temporary storage root |
| `LIPSYNC_TTL` | `86400` | Idle lifetime of unsubmitted/finished sessions, seconds |
| `LIPSYNC_CAPACITY` | `32` | Total session capacity |
| `LIPSYNC_MAX_FILE` | `5368709120` | Per-input byte limit |
| `LIPSYNC_CHUNK_SIZE` | `8388608` | Per-upload-request byte limit |

The Python application defaults to MuseTalk. `start.sh` / `start.ps1` explicitly select it and clears legacy `LIPSYNC_COMMAND`. Custom deployments may retain `LIPSYNC_ENGINE=command` with a JSON argument array in `LIPSYNC_COMMAND`, containing `{video}`, `{audio}` and `{output}` absolute-path placeholders. This compatibility adapter is not needed for normal MuseTalk deployment.

## Server baseline and GPU acceptance

Inside the original successful Conda environment, run the project script. It reads the original checkout and writes records only into this project:

```bash
conda activate musetalk
bash scripts/capture_baseline.sh "$HOME/apps/MuseTalk"
```

Compare `deployment-records/server-baseline/musetalk.commit.txt` with `third_party/MuseTalk.source.json`, then compare dependencies and weight hashes. Differences must be evaluated before claiming reproduction. New deployment environment records are saved in `deployment-records/`.

After installation, run two consecutive actual GPU jobs on the original test media:

```bash
.conda/musetalk/bin/python scripts/smoke_musetalk.py \
  --video /absolute/path/speaker.mp4 \
  --audio /absolute/path/voice.wav \
  --output "$PWD/storage/verification/speaker_synced.mp4"
```

Outputs must use new filenames. The script checks decoding, streams and durations, and records elapsed time, worker PID and hashes in `.verification.json`. Play both outputs to review lip sync, face blending and audio against the original successful result. Then test the complete HTTP upload-to-download path with the real model. Initialization alone is not acceptance.

For original MuseTalk test inputs with different video/audio durations, add `--timeline audio`:

```bash
.conda/musetalk/bin/python scripts/smoke_musetalk.py \
  --video "$HOME/apps/MuseTalk/test_inputs/speaker.mp4" \
  --audio "$HOME/apps/MuseTalk/test_inputs/voice.wav" \
  --output "$PWD/storage/verification/speaker_audio.mp4" \
  --timeline audio
```

Audio mode renders to the complete audio duration, quantized to source video frames. Longer audio repeats frames in upstream MuseTalk's forward/reverse cycle; shorter audio generates a shorter result. Inputs are unchanged. Decode, expected output duration and audio-stream checks remain enabled, and the report records the selected timeline.
The default `--timeline strict` preserves the source timeline and rejects differences over 150 ms before model loading, showing both durations. Audio mode is available for local model verification and direct Python engine calls; the HTTP service and VideoPrinterTurbo client retain their strict timeline contract.

## VideoPrinterTurbo client

The client interface is unchanged. In VideoPrinterTurbo's `server/` directory:

```python
import os
from pipeline.lip_sync.remote import RemoteLipSync

service = RemoteLipSync(
    base_url="http://192.168.1.100:8081",
    api_key=os.environ.get("LIPSYNC_API_KEY", ""),
    request_timeout=60,
    poll_interval=2,
    job_timeout=7200,
    chunk_size=8 * 1024 * 1024,
    retries=3,
    retry_delay=1,
)
result = service.lip_sync("speaker.mp4", "final_voice.wav", "speaker_synced.mp4")
```

Transfers support resume, idempotent submission and SHA-256 validation. Checkpoints never store keys. A failed job requires removing the checkpoint or choosing a new output to explicitly retry. This change does not modify the client repository, PipelineManager, database or frontend.

## Development verification

`pyproject.toml` and `uv.lock` cover lightweight service development. GPU deployment uses `deployment/` and `install.sh`; `uv sync` alone does not install the model stack. Transport regression tests read the actual client from sibling `../VideoPrinterTurbo/server`, without modifying it.

```bash
mkdir -p .runtime/tmp
UV_CACHE_DIR="$PWD/.cache/uv" PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/.runtime/tmp" \
  uv run --with pytest --with httpx --with requests python -m pytest -q tests --basetemp=.runtime/pytest
```

Fake copying engines only verify protocol and lifecycle behavior, not lip-sync quality. Sandboxes that disallow local ports skip TCP cases.

Source attribution: [third_party/README.md](third_party/README.md). Historical standalone installation instructions remain in `doc/MuseTalk/INSTALL.md`; new deployments use this README.
