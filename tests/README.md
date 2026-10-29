# Tests

Run commands from the VideoPrinterTurbo_LipSync project root.

## CPU unit tests

These tests need the project's base dependencies, but no model weights, CUDA,
PyTorch, OpenCV or a network connection. Most external I/O is mocked and filesystem
checks use temporary directories. `test_media_stream_unit.py` also exercises real
FFmpeg when available; its FFmpeg cases skip when the tools are missing.

```bash
.venv/bin/python -m unittest discover -s tests -p '*_unit.py' -v
.venv/bin/python -m unittest discover -s tests -p test_face_boxes.py -v
```

| File | Coverage |
| --- | --- |
| test_config_unit.py | Environment configuration, command placeholders, request validation |
| test_engine_unit.py | Hashing, media durations, timeline checks, result validation, command failure |
| test_assets_unit.py | Weight integrity, range downloads, retries, partial files, HTTP 416 |
| test_service_unit.py | Capacity, incomplete/corrupt uploads, idempotency, cleanup guards, worker outcomes |
| test_runtime_unit.py | Frame selection, invalid timestamps, workspace protection, worker protocol failures |
| test_routes_unit.py | Bearer authentication, If-Range, reader release on errors and cancellation |
| test_errors_unit.py | Worker-to-engine-to-job error propagation and bounded diagnostics |
| test_face_boxes.py | Edge clipping, invalid boxes, frame diagnostics and preprocessing cleanup |
| test_media_stream_unit.py | Real FFmpeg 4K encoding, fractional seeks, subprocess inactivity and cleanup |
| test_audio_streaming.py | Global token alignment across 30-second boundaries and bounded CPU cache |
| test_memory_frames.py | No PNG intermediates, forward decoder reuse and reversed block ordering |
| test_performance_paths.py | Proxy coordinate mapping, full-size miss fallback, batched VAE contracts and timing attribution |
| test_landmarks.py | All-missing/mixed face blocks bypass the upstream summary division by zero |
| test_missing_faces_unit.py | Original-frame passthrough, mixed/empty inference batches and audio indices in both timeline modes |

## Full regression suite

Install test dependencies in a network-enabled environment:

```bash
uv sync --extra test --extra test-runtime
.venv/bin/python -m pytest -q --cov=lipsync_service --cov=routers --cov=app --cov-report=term-missing
```

The existing integration suite uses ffmpeg/ffprobe with libx264 and the sibling
`../VideoPrinterTurbo/server` checkout to test the actual remote adapter. Some tests
start localhost TCP servers and child processes. Tests using torch/OpenCV or
PowerShell skip when those dependencies are absent. The `test-runtime` extra adds
CPU-compatible torch/numpy/Pillow for the mocked runtime tests. Those tests use
OpenCV when available and otherwise use a Pillow adapter; they do not validate
OpenCV kernels or the real face models.
The Windows helper tests include emulation; actual Windows process-tree behavior
still needs a Windows runner.

Passing mocked tests does not establish lip-sync quality, successful CUDA model
loading, GPU memory requirements, or performance on real footage. Validate those
separately on the target server with the real weights and smoke_musetalk script.
