"""Application configuration, lifecycle, and router registration."""
import asyncio
import json
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from lipsync_service.engine import CommandEngine
from lipsync_service.musetalk_engine import MuseTalkEngine, ROOT
from lipsync_service.service import Service
from routers.index import router as index_router
from routers.lipsync import router as lipsync_router


def positive_env(name, default, cast=float):
    value = cast(os.environ.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')
    return value


def create_app(*, engine=None, storage=None, api_key=None, ttl=None, capacity=None,
               max_file=None, chunk_size=None):
    key = api_key if api_key is not None else os.environ.get('LIPSYNC_API_KEY', '')
    command = json.loads(os.environ.get('LIPSYNC_COMMAND', '[]'))
    if not isinstance(command, list) or any(not isinstance(arg, str) for arg in command):
        raise ValueError('LIPSYNC_COMMAND must be a JSON array of command arguments')
    if command and not all(any('{' + name + '}' in arg for arg in command)
                           for name in ('video', 'audio', 'output')):
        raise ValueError('LIPSYNC_COMMAND requires {video}, {audio}, and {output} placeholders')
    backend = os.environ.get('LIPSYNC_ENGINE', 'musetalk')
    if backend not in ('musetalk', 'command'):
        raise ValueError('LIPSYNC_ENGINE must be musetalk or command')
    if engine is not None:
        configured_engine = engine
    elif backend == 'command' or command:
        configured_engine = CommandEngine(command, positive_env('LIPSYNC_INFERENCE_TIMEOUT', 3600))
    else:
        precision = os.environ.get('LIPSYNC_FLOAT16', '1')
        if precision not in ('0', '1'):
            raise ValueError('LIPSYNC_FLOAT16 must be 0 or 1')
        configured_engine = MuseTalkEngine(
            model_dir=os.environ.get('LIPSYNC_MODEL_DIR', str(ROOT / 'models')),
            gpu=os.environ.get('LIPSYNC_GPU', '0'),
            batch_size=positive_env('LIPSYNC_BATCH_SIZE', 1, int),
            use_float16=precision == '1',
            timeout=positive_env('LIPSYNC_INFERENCE_TIMEOUT', 3600),
            startup_timeout=positive_env('LIPSYNC_STARTUP_TIMEOUT', 600))

    @asynccontextmanager
    async def lifespan(app):
        service = Service(configured_engine,
                          Path(storage or os.environ.get('LIPSYNC_STORAGE', 'storage')).resolve(),
                          ttl=ttl if ttl is not None else positive_env('LIPSYNC_TTL', 86400),
                          capacity=capacity if capacity is not None else positive_env('LIPSYNC_CAPACITY', 32, int),
                          max_file=max_file if max_file is not None else positive_env('LIPSYNC_MAX_FILE', 5 * 1024 ** 3,
                                                                                      int),
                          chunk_size=chunk_size if chunk_size is not None else positive_env('LIPSYNC_CHUNK_SIZE',
                                                                                            8 * 1024 ** 2, int))
        app.state.service = service
        try:
            if hasattr(configured_engine, 'start'):
                await asyncio.to_thread(configured_engine.start)
            service.start()
            yield
        finally:
            await asyncio.to_thread(service.close)

    app = FastAPI(title='VideoPrinterTurbo LipSync', version='0.2.0', lifespan=lifespan)
    app.state.api_key = key
    app.state.engine = configured_engine
    app.include_router(index_router)
    app.include_router(lipsync_router)
    return app


app = create_app()
