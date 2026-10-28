# LipSync HTTP contract v1

Single Uvicorn worker only. Metadata and queue are in memory; restart invalidates all IDs.
Optional `Authorization: Bearer <LIPSYNC_API_KEY>` applies to every `/v1` endpoint.
Success JSON: `{code: 0, msg: "success", data: ...}`. Errors use HTTP status and `{detail: ...}`.

| Method | Path | Body / result |
| --- | --- | --- |
| GET | /health | No auth; `{ready, timelines: ["strict", "audio"]}`; 200 if ready, otherwise 503; strict-only engines advertise only strict |
| POST | /v1/uploads | `{video: {name,size,sha256}, audio: {name,size,sha256}}` → session |
| GET | /v1/uploads/{id} | session, including authoritative offsets and optional job_id |
| PATCH | /v1/uploads/{id}/{video\|audio} | raw octets, `Upload-Offset` header → session |
| POST | /v1/uploads/{id}/submit | optional `{timeline: "strict"|"audio"}`; verifies files, idempotently enqueues → job |
| GET | /v1/jobs/{id} | job status `queued/running/succeeded/failed`, error, result |
| GET | /v1/jobs/{id}/result | MP4, single HTTP byte Range/If-Range supported; 409 until succeeded |

Session: `{upload_id, files: {video: {size,sha256,offset}, audio: ...}, job_id}`.
Job: `{job_id, upload_id, status, error, timeline, expected_duration, result: null|{size,sha256}}`.

For failed jobs, `error` remains a string. MuseTalk inference failures include the
underlying exception type and message (including frame/box diagnostics when
available), limited to 2000 characters. Full tracebacks remain in the task's
`inference.log`. Subprocess commands and raw subprocess logs are not returned.
Existing clients that display `job.error` receive these details without changes.
Only newly executed jobs use the improved diagnostics; old failed jobs retain
their original stored error.
`timeline` defaults to `strict` for an absent body. Retrying submit with the same mode
returns the original job, including failed jobs; a different mode returns 409.
`expected_duration` is null while queued/before input validation, otherwise seconds.
Strict mode preserves source video duration; audio mode renders to full audio duration,
quantized to source frames, using MuseTalk's forward/reverse cycle when needed.
Duration checks allow 150 ms; low-FPS inputs that cannot represent the requested audio
duration within that tolerance fail explicitly. Frame counts use rational FPS arithmetic.
Mode is immutable per job. Engines without audio support reject audio submission with 422.
The health response additionally advertises `timelines` (supported modes), including
while recovering. Clients must check audio support before submitting audio-mode work.
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
MuseTalk requires constant frame rate video. Default strict mode requires aligned audio
(difference at most 150 ms) and preserves source frame count/order and fractional rate.
Explicit audio mode accepts different durations and uses the audio timeline. Both modes
reject frames without a detected face. Mode switches reuse the loaded model.
Model quality still requires manual inspection. `LIPSYNC_ENGINE=command` retains the
legacy adapter contract for custom deployments and protocol tests.

Client checkpoint stores endpoint, input fingerprints, upload ID and job ID, never keys.
It includes timeline identity. Legacy checkpoints with no mode migrate only for strict;
changing mode starts a new session. Audio clients require explicit health capability and
matching job mode/duration. New strict clients accept legacy strict job metadata.
404 starts a fresh upload. Local timeout preserves checkpoint; remote work may continue.
Success removes the checkpoint and atomically publishes a verified `.part` MP4 without
replacing existing output. Pipeline integration and frontend changes are out of scope.
Client readiness waiting defaults to 600 seconds, bounded by the 7200-second job budget.
The budget starts after input preflight/hashing and covers transfers, queue/model waiting
and result validation. API 503 recovery queries health with a bounded deadline; other
retryable network errors retain the configured retry count. Auth/input errors fail fast.
