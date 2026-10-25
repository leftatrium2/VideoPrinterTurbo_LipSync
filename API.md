# LipSync HTTP contract v1

Single Uvicorn worker only. Metadata and queue are in memory; restart invalidates all IDs.
Optional `Authorization: Bearer <LIPSYNC_API_KEY>` applies to every `/v1` endpoint.
Success JSON: `{code: 0, msg: "success", data: ...}`. Errors use HTTP status and `{detail: ...}`.

| Method | Path | Body / result |
| --- | --- | --- |
| GET | /health | No auth; `{ready: true}` (200) or `{ready: false}` (503) |
| POST | /v1/uploads | `{video: {name,size,sha256}, audio: {name,size,sha256}}` → session |
| GET | /v1/uploads/{id} | session, including authoritative offsets and optional job_id |
| PATCH | /v1/uploads/{id}/{video\|audio} | raw octets, `Upload-Offset` header → session |
| POST | /v1/uploads/{id}/submit | verifies both files, idempotently enqueues → job |
| GET | /v1/jobs/{id} | job status `queued/running/succeeded/failed`, error, result |
| GET | /v1/jobs/{id}/result | MP4, single HTTP byte Range/If-Range supported; 409 until succeeded |

Session: `{upload_id, files: {video: {size,sha256,offset}, audio: ...}, job_id}`.
Job: `{job_id, upload_id, status, error, result: null|{size,sha256}}`.
`name` supplies only a validated file extension; server chooses all paths and IDs.
PATCH is sequential per file; wrong offset returns 409 without modifying it. Chunk limit
is 8 MiB by default. Incomplete transfers return 409 on submit; checksum mismatch 422.
Interrupted PATCH may have accepted any prefix: always GET the authoritative offset
before retrying. Unsubmitted uploads and finished jobs expire after configurable idle TTL.
Queued/running jobs do not expire. GET and downloads refresh TTL.
Submission after a lost response returns the same job; failed jobs are not re-enqueued.
Unknown/expired/restarted IDs return 404. Capacity exhaustion returns 429; missing model
engine readiness returns 503 on submit. Download metadata uses the immutable output SHA-256.

The default engine is the bundled MuseTalk 1.5 (`v15`), loaded before HTTP startup.
Initialization failure aborts startup and releases the storage lock. The health endpoint
reports engine readiness; `GET /` retains its original success response. During worker
recovery health and new submissions may return 503; queued jobs remain queued until
the serial worker reaches them. A failed job is never retried automatically. A supervisor
also reloads workers that exit while idle, retrying failed recovery every 30 seconds.
MuseTalk accepts constant frame rate video and aligned audio (duration difference at most
150 ms). It preserves source frame count/order and fractional frame rate, without cycling
or retiming video. Variable frame rate and frames without a detected face fail the job.
Model quality still requires manual inspection. `LIPSYNC_ENGINE=command` retains the
legacy adapter contract for custom deployments and protocol tests.

Client checkpoint stores endpoint, input fingerprints, upload ID and job ID, never keys.
404 starts a fresh upload. Local timeout preserves checkpoint; remote work may continue.
Success removes the checkpoint and atomically publishes a verified `.part` MP4 without
replacing existing output. Pipeline integration and frontend changes are out of scope.
