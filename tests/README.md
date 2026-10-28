# Tests

Run commands from the VideoPrinterTurbo_LipSync project root.

## CPU unit tests

These tests need the project's base dependencies, but no model weights, CUDA,
PyTorch, OpenCV, network connection, or ffmpeg executable. External processes and
network requests are mocked; filesystem checks use temporary directories.

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

## Full regression suite

Install test dependencies in a network-enabled environment:

```bash
uv sync --extra test
.venv/bin/python -m pytest -q --cov=lipsync_service --cov=routers --cov=app --cov-report=term-missing
```

The existing integration suite uses ffmpeg/ffprobe with libx264 and the sibling
`../VideoPrinterTurbo/server` checkout to test the actual remote adapter. Some tests
start localhost TCP servers and child processes. Tests using torch/OpenCV or
PowerShell skip when those dependencies are absent. Install CPU torch, numpy and
opencv-python to execute the mocked runtime generation tests in test_musetalk.py.
The Windows helper tests include emulation; actual Windows process-tree behavior
still needs a Windows runner.

Passing mocked tests does not establish lip-sync quality, successful CUDA model
loading, GPU memory requirements, or performance on real footage. Validate those
separately on the target server with the real weights and smoke_musetalk script.
