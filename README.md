<div align="center">
<h1 align="center">VideoPrinterTurbo_LipSync</h1>
<br>
<h3>English | <a href="README_cn.md">简体中文</a></h3>
<br>
</div>
# VideoPrinterTurbo_LipSync

A local LipSync backend for VideoPrinterTurbo, using Python 3.11–3.12, uv, FastAPI, and Uvicorn. It provides an API only, with no frontend.

## Starting the service

Dependencies are already configured. The following startup command has been verified on the current machine (run from the project root):

```sh
/Users/sunxiao5/.local/bin/uv run /Users/sunxiao5/opensource/agent/VideoPrinterTurbo_LipSync/.venv/bin/python -m uvicorn app:app --port 8081
```

By default, the service listens only on the local machine. Port 8081 avoids a conflict with VideoPrinterTurbo's port 8080.
For access from other machines, set `--host` to `0.0.0.0`.

## API

```sh
curl http://127.0.0.1:8081/
```

`GET /` returns:

```json
{"code": 0, "msg": "success", "data": {}}
```

Automatically generated FastAPI documentation: <http://127.0.0.1:8081/docs>.

## Project structure

- `app.py`: Application configuration, lifecycle, application factory, and router registration.
- `routers/index.py`: Root endpoint; `routers/lipsync.py`: Upload, job, result download APIs, and authentication.
- `lipsync_service/`: Inference command execution, job queue, and storage management.
- `utils/`: Response wrappers and shared constants, following VideoPrinterTurbo's success response format.
- `pyproject.toml`: uv dependency configuration; `uv.lock` is generated after the first successful `uv sync`.

The service supports resumable uploads, a serial job queue, resumable result downloads, and command-based model integration. See [API.md](API.md) for the HTTP contract.
Models and weights must be installed separately on a CUDA machine. This repository does not include a model and does not pass off ordinary audio/video muxing as lip synchronization.

## CUDA service deployment

Run `uv sync` in the project directory, install FFmpeg/ffprobe, and ensure they are on PATH.
Use the model's own Python environment and inference script, and configure the command as a JSON argument array:

```sh
export LIPSYNC_COMMAND='["/opt/model/.venv/bin/python", "/opt/model/run_lipsync.py", "--video", "{video}", "--audio", "{audio}", "--output", "{output}"]'
export LIPSYNC_API_KEY='replace-with-your-own-key'
uv run python -m uvicorn app:app --host 0.0.0.0 --port 8081 --workers 1
```

The script path and arguments above are **an example of the integration contract**. Replace them with your actual model script; this script is not included in the repository.
The command must include `{video}`, `{audio}`, and `{output}`, which the service replaces with absolute paths. No shell is launched.
The script must block until inference finishes, exit with code 0 on success, and write a nonempty MP4 to `{output}`.
Use absolute paths for the script's configuration and weights; its working directory is the directory from which this service was started.
Inputs are already aligned. Do not alter the timeline by looping, changing speed, or truncating them. The backend checks the video duration and performs a full decode,
then preserves the model's video frames, remuxes the uploaded voiceover as the audio track, and validates the result. The lip-sync quality of the model output still requires manual review.

Without a configured command, the root endpoint remains available, but job submission returns HTTP 503.
Model errors, missing FFmpeg, missing output, duration changes, or decoding failures put the job in the `failed` state, with no result available for download.
The job ID and exception type are written to the service log. Model stdout/stderr are saved to `inference.log` in the job's temporary directory
and deleted when the job expires. HTTP errors do not expose the model command or logs.

| Environment variable | Default | Description |
| --- | --- | --- |
| `LIPSYNC_COMMAND` | `[]` | Inference command as a JSON array |
| `LIPSYNC_API_KEY` | Empty | When nonempty, `/v1` endpoints require a Bearer token |
| `LIPSYNC_STORAGE` | `storage` | Root directory for temporary files, relative to the startup directory |
| `LIPSYNC_INFERENCE_TIMEOUT` | 3600 | Timeout for a single model command, in seconds |
| `LIPSYNC_TTL` | 86400 | Idle retention period for unsubmitted uploads and finished jobs, in seconds |
| `LIPSYNC_CAPACITY` | 32 | Maximum total number of sessions, including unsubmitted and finished sessions |
| `LIPSYNC_MAX_FILE` | 5368709120 | Maximum size of each input file, in bytes |
| `LIPSYNC_CHUNK_SIZE` | 8388608 | Maximum size of a single upload request, in bytes |

Run only one worker. A process lock protects the storage directory, so a second instance using the same directory fails to start. Deploy only one instance per GPU as well.
Metadata and the queue are held only in memory. After a restart, old IDs return 404; leftover runtime files in the storage directory are cleaned up at startup.
On graceful shutdown, the service waits for the current job to finish (or for the command to time out), discards the remaining queue, and cleans up temporary files.
Running jobs and active downloads are exempt from TTL cleanup. Progress queries and downloads refresh the retention period.
Uploads and downloads can resume after network interruptions. After a server restart, recovery requires uploading and submitting again; previous inference jobs are not resumed.

## Calling from VideoPrinterTurbo

From VideoPrinterTurbo's `server/` directory:

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

Configuration is passed explicitly through the constructor, consistent with the existing cloud adapters, without changing database or frontend configuration.
HTTP(S) is supported for self-hosted service URLs. The client connects directly and ignores proxy environment variables.
If you lower the server's chunk size limit, lower the client's `chunk_size` accordingly.
The output must be a new `.mp4` path. Inputs must share the same start time, with a duration difference of no more than 150 ms.
Use a separate client instance and a different output path for each concurrent call.

The client saves a `.lipsync.json` checkpoint and a `.lipsync.part` download file alongside the result, without storing the API key.
After an interruption, call again with the same inputs, service URL, and output path to resume. Changing the inputs automatically creates a new session.
When inputs change or a session expires, old server-side files remain until TTL cleanup. Failed jobs retain their original IDs and do not automatically rerun inference.
To explicitly retry a failed job, remove the local checkpoint or use a new output path.
`last_job_id` provides the most recent job ID. A local timeout does not cancel the remote job.
On success, the client verifies SHA-256, audio/video streams, duration, and full decoding, atomically publishes the result, and deletes the checkpoint and temporary download file.
`.lipsync.lock` is a lightweight lock file that is kept on disk; its presence does not mean a job is still running. The OS automatically releases the lock after an abnormal exit.
Integration with `PipelineManager` is not yet implemented; no new job fields or frontend entry points are added.

## Verification

This repository's tests call the real client code in the adjacent `../VideoPrinterTurbo/server` directory, so the two repositories must be siblings.
Use an environment with pytest/httpx/requests installed, or run:

```sh
uv run --with pytest --with httpx --with requests python -m pytest -q tests
```

Tests cover both in-process ASGI transport and real Uvicorn/TCP connections. TCP cases are skipped in sandboxes that prohibit listening on ports.
The test engine uses FFmpeg to copy video frames. **It verifies only the protocol, queue, file handling, and result delivery, not lip-sync quality.**
Coverage includes upload offsets and validation, idempotent recovery after lost responses, client restarts, invalidated server sessions, Range/If-Range,
resumable downloads and corruption detection, serial queuing, model failures/timeouts, client timeouts, TTL cleanup, and process locking.
