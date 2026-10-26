"""LipSync upload, job status, and result download routes."""
import hmac
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from utils.result import result_succ


class ResultResponse(FileResponse):
    """Starlette 0.41 computes If-Range ETags from stat, ignoring custom ETags."""
    def __init__(self, *args, release, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = release

    def _should_use_range(self, http_if_range, stat_result):
        return http_if_range in (self.headers.get('etag'), self.headers.get('last-modified'))

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release()


class FileSpec(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, strict=True)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @field_validator('name')
    @classmethod
    def safe_name(cls, value):
        if '/' in value or '\\' in value or Path(value).suffix.lower() not in {
            '.mp4', '.mov', '.mkv', '.webm', '.avi', '.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg'}:
            raise ValueError('Expected a media filename with a supported extension')
        return value


class UploadSpec(BaseModel):
    video: FileSpec
    audio: FileSpec


class SubmitSpec(BaseModel):
    timeline: Literal['strict', 'audio'] = 'strict'


def auth(request: Request, authorization: str | None = Header(default=None)):
    key = request.app.state.api_key
    if key and not hmac.compare_digest((authorization or '').encode(), f'Bearer {key}'.encode()):
        raise HTTPException(401, 'Invalid API key')


router = APIRouter(prefix='/v1', dependencies=[Depends(auth)])


@router.post('/uploads')
def create(spec: UploadSpec, request: Request):
    return result_succ(request.app.state.service.create(spec.model_dump()))


@router.get('/uploads/{uid}')
def get_upload(uid: str, request: Request):
    return result_succ(request.app.state.service.view(uid))


@router.patch('/uploads/{uid}/{kind}')
async def patch(uid: str, kind: str, request: Request, upload_offset: int = Header(ge=0)):
    service = request.app.state.service
    if kind not in ('video', 'audio'):
        raise HTTPException(404, 'Unknown media kind')
    with service.lock:
        upload = service.get(uid)
        if not upload.lock.acquire(blocking=False):
            raise HTTPException(409, 'Another upload operation is in progress')
    try:
        if upload.job_id:
            raise HTTPException(409, 'Submitted files are immutable')
        item = upload.files[kind]
        if item['path'].stat().st_size != upload_offset:
            raise HTTPException(409, 'Upload offset mismatch; query the session')
        remaining = item['meta']['size'] - upload_offset
        limit = min(service.chunk_size, remaining)
        length = request.headers.get('content-length')
        if length is not None and (not length.isdigit() or int(length) > limit):
            raise HTTPException(413, 'Chunk exceeds file size or chunk limit')
        received = 0
        with item['path'].open('ab') as target:
            async for chunk in request.stream():
                if received + len(chunk) > limit:
                    raise HTTPException(413, 'Chunk exceeds file size or chunk limit')
                target.write(chunk)
                received += len(chunk)
        if received == 0:
            raise HTTPException(400, 'Empty chunk')
        return result_succ(upload.view())
    finally:
        upload.touched = time.monotonic()
        upload.lock.release()


@router.post('/uploads/{uid}/submit')
def submit(uid: str, request: Request, spec: SubmitSpec | None = None):
    return result_succ(request.app.state.service.submit(uid, spec.timeline if spec else 'strict'))


@router.get('/jobs/{jid}')
def get_job(jid: str, request: Request):
    return result_succ(request.app.state.service.job(jid))


@router.get('/jobs/{jid}/result')
def result(jid: str, request: Request):
    service = request.app.state.service
    with service.lock:
        job = service.job(jid)
        if job['status'] != 'succeeded':
            raise HTTPException(409, 'Result not available')
        range_header = request.headers.get('range', '')
        if len(range_header) > 128 or ',' in range_header:
            raise HTTPException(416, 'Only a single byte range is supported')
        upload = service.get(job['upload_id'])
        upload.readers += 1
        def release():
            with service.lock:
                upload.readers -= 1
                upload.touched = time.monotonic()
        return ResultResponse(upload.directory / 'result.mp4', media_type='video/mp4',
                            headers={'ETag': '"' + job['result']['sha256'] + '"'},
                            release=release)

