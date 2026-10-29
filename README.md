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

Both installation and startup verify all 75 bundled source files. Installation checks them before downloading Miniforge or installing dependencies, so missing `musetalk/models/*.py` fails immediately. Model initialization failure prints the worker log tail; manual inspection: `Get-Content .runtime\musetalk\worker.log -Tail 160`. Batch entry points select 64-bit PowerShell and preserve failure exit codes.

For real video verification, use the Windows wrapper that also sets Conda DLL/FFmpeg paths:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\smoke_musetalk.ps1 -Video "D:\media\speaker.mp4" -Audio "D:\media\voice.wav" -Output "storage\verification\speaker_audio.mp4" -Timeline audio
```

`-Timeline audio` corresponds to Linux's `--timeline audio` and accepts different input durations. It runs twice by default; add `-Repeat 1` for one run. Outputs must use new filenames. The default strict mode retains the 150ms duration requirement; that rejection is not a Windows installation failure.

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

`GET /` retains `{code:0,msg:"success",data:{}}`. `/health` returns `ready` and supported `timelines`, with HTTP 200 when ready or HTTP 503 while unavailable. Interactive API documentation is at `/docs`; see [API.md](API.md) for transfer semantics.

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

Videos must have a constant frame rate and detectable faces. Default strict tasks require aligned inputs within 150 ms and preserve frame count/order/rate. Explicit audio tasks render to the complete audio duration, repeating source frames forward/reverse or ending output earlier as needed. Both modes retain fractional FPS sampling, full decode, expected duration and uploaded-audio checks. The same loaded worker handles alternating modes. Visual lip-sync quality requires manual review.

Inference preserves the input display resolution. Frames are decoded and detected in bounded blocks (1–32 frames, sized from a pixel memory budget), full-resolution arrays pass directly through memory without temporary PNGs, and a single encoder writes the output continuously. Forward blocks reuse the decoder; reverse cycles seek by block. Missing-face frames pass through unchanged. Output uses H.264 CRF 18; resolution preservation is not lossless encoding, and the existing BGR24 pipeline does not preserve HDR/high bit depth.

Whisper features keep the original 30-second boundaries, cache at most two segments on CPU, and transfer only the current inference batch to GPU. Global audio indices never restart at video block boundaries. Disk usage includes the input, output and temporary 16 kHz mono float32 PCM (about 220 MiB/hour). The frame budget is not a hard RSS cap: model/decoder/encoder/single-frame workspaces are additional. Frame-cache memory no longer grows with clip duration.

Timestamp validation streams frame records with an inactivity deadline instead of a fixed 120-second total deadline. The MuseTalk worker defaults to no total inference limit, with an independent progress deadline. Jobs use isolated temporary directories and TTL cleanup. Restart invalidates old IDs and clears abandoned files. Client job timeouts remain independent and must cover long processing/queue times.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `LIPSYNC_HOST` | `0.0.0.0` | Linux/Windows launcher bind address; overridable with the environment variable |
| `LIPSYNC_PORT` | `8081` | Launcher port |
| `LIPSYNC_GPU` | `0` | CUDA_VISIBLE_DEVICES, normally one GPU index |
| `LIPSYNC_BATCH_SIZE` | `1` | Inference batch size |
| `LIPSYNC_FLOAT16` | `1` | 1 for FP16, 0 for FP32 |
| `LIPSYNC_MODEL_DIR` | project/models | Model root; prefer an absolute path |
| `LIPSYNC_STARTUP_TIMEOUT` | `600` | Model initialization timeout, seconds |
| `LIPSYNC_INFERENCE_TIMEOUT` | `0` | MuseTalk worker total limit in seconds; 0 disables it. Command backend defaults to 3600 and requires a positive value |
| `LIPSYNC_IDLE_TIMEOUT` | `600` | Worker inactivity deadline, reporting the last stage |
| `LIPSYNC_MEDIA_IDLE_TIMEOUT` | `120` | Media output/frame I/O/finalization inactivity deadline |
| `LIPSYNC_FRAME_MEMORY_MB` | `256` | Pixel memory budget in MiB, reserving three pixel buffers per frame |
| `LIPSYNC_DETECT_MAX_SIDE` | `1280` | Detection/pose proxy max side; 0 uses full size. Coordinates map back to the original image, and proxy misses retry full-size detection |
| `LIPSYNC_PROFILE` | `1` | Stage wall times and processing FPS; GPU stages synchronize for attribution. 0 disables profiling and its synchronization overhead |
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
The default `--timeline strict` preserves the source timeline and rejects differences over 150 ms before model loading, showing both durations. Audio mode is also available through HTTP and RemoteLipSync. Update client and server together; removing client validation alone does not enable it.

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
    readiness_timeout=600,
    timeline="strict",  # Explicitly choose "audio" for different durations
    chunk_size=8 * 1024 * 1024,
    retries=3,
    retry_delay=1,
)
result = service.lip_sync("speaker.mp4", "final_voice.wav", "speaker_synced.mp4")
```

Transfers support resume, idempotent submission and SHA-256 validation. Checkpoints never store keys. A failed job requires removing the checkpoint or choosing a new output to explicitly retry. This change updates the remote client and HTTP contract; PipelineManager, database and frontend remain outside its scope.

## Development verification

`pyproject.toml` and `uv.lock` cover lightweight service development. GPU deployment uses `deployment/` and `install.sh`; `uv sync` alone does not install the model stack. Transport regression tests read the actual client from sibling `../VideoPrinterTurbo/server`, without modifying it.

```bash
mkdir -p .runtime/tmp
UV_CACHE_DIR="$PWD/.cache/uv" PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/.runtime/tmp" \
  uv run --with pytest --with httpx --with requests python -m pytest -q tests --basetemp=.runtime/pytest
```

Fake copying engines only verify protocol and lifecycle behavior, not lip-sync quality. Sandboxes that disallow local ports skip TCP cases.

Source attribution: [third_party/README.md](third_party/README.md). Historical standalone installation instructions remain in `doc/MuseTalk/INSTALL.md`; new deployments use this README.


## Remote budgets and acceptance

RemoteLipSync supports explicit strict/audio timelines and a 600-second readiness timeout. Audio jobs require advertised server capability and matching job mode/duration. Missing submit bodies remain strict; same-mode resubmission is idempotent, changing mode on a submitted upload returns 409. Checkpoints include mode identity; only strict can migrate legacy mode-less checkpoints.

The 60-second request timeout limits individual HTTP requests. The readiness timeout limits model startup/recovery waiting. The 7200-second job budget starts after input preflight/hashing and covers readiness, upload, queue/model waiting, download and verification. Recovery from 503 uses readiness and job deadlines rather than only three short retries. Auth/input errors fail immediately. Local timeout preserves checkpoints without cancelling remote work.

On the measured RTX 3080, 12.564 seconds of media took about 279–316 seconds to process. Illustrative budgets: up to one minute, server inference timeout 3600 / client job timeout 7200; three to five minutes, 14400 / 21600. Keep readiness at 600 and account for preceding queued work separately. Measure on the target machine; these values are not throughput guarantees. Restart the server after changing its inference timeout.

Result publication uses hard links where supported, otherwise a platform exclusive atomic rename; permission, disk-space and existing-file errors are preserved, and partial downloads/checkpoints remain resumable.

From sibling VideoPrinterTurbo's `server/`, with FFmpeg/ffprobe and its Python dependencies available:

```bash
python -m pipeline.lip_sync.check_remote \
  --base-url http://192.168.1.100:8081 \
  --video /absolute/path/speaker.mp4 --audio /absolute/path/voice.wav \
  --output /absolute/path/new_synced.mp4 --timeline audio
```

Use the actual LAN IP. Credentials are read from LIPSYNC_API_KEY; the generated `.remote-verification.json` contains no key and records mode, job ID, elapsed time, durations and SHA-256. Use aligned inputs for strict acceptance and play the output to inspect lip quality.


## Performance diagnostics

The production frame path uses in-memory arrays instead of PNG encoding/writing/reading.
Detection and pose estimation default to a 1280-pixel proxy, with independent X/Y
coordinate mapping to the full-resolution crop/composite. Proxy misses retry the
original frame; set `LIPSYNC_DETECT_MAX_SIDE=0` for full-size detection. Proxy detection
can change boxes, so inspect small-face/occlusion cases. Output resolution is unchanged.
No frame tracking, detection skipping or cached masks are used. VAE masking,
normalization, sampling and channel order follow upstream, but masked/reference
encoding uses two batched forwards rather than two forwards per frame. Batch size
now affects VAE as well as UNet VRAM usage; start with 2 and measure.

`MuseTalk timing:` lines appear in the task's `inference.log` about every 30 seconds
and at completion, with cumulative stage seconds, GPU allocated memory and
`processing_fps`. Processing FPS excludes upload, model readiness and timestamp
preflight, but includes audio preparation and encoder backpressure. Encoder thread
work may overlap other stages; write time is not total encoder CPU time. Use
`LIPSYNC_PROFILE=0` after diagnosis to remove timing/synchronization overhead.
No CUDA FPS or speedup is claimed without measurement on the deployment server.
