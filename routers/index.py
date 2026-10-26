from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from utils.result import result_succ


router = APIRouter()


@router.get("/")
async def root():
    return result_succ()


@router.get('/health')
async def health(request: Request):
    ready = bool(request.app.state.engine.ready)
    timelines = ['strict', 'audio'] if hasattr(request.app.state.engine, 'run_timeline') else ['strict']
    return JSONResponse({'ready': ready, 'timelines': timelines}, status_code=200 if ready else 503)
