"""Bounded inference diagnostics shared across the worker and HTTP boundary."""
import subprocess


class InferenceError(RuntimeError):
    """An inference failure whose diagnostic may be returned to the caller."""


def inference_detail(exc):
    # Subprocess exception strings can contain entire commands and their secrets.
    if isinstance(exc, subprocess.CalledProcessError):
        message = f'Media/model subprocess failed with exit code {exc.returncode}'
    elif isinstance(exc, subprocess.TimeoutExpired):
        message = f'Media/model subprocess timed out after {exc.timeout}s'
    elif isinstance(exc, OSError):
        message = exc.strerror or type(exc).__name__
    else:
        message = str(exc) or type(exc).__name__
    message = ' '.join(message.split())
    return f'{type(exc).__name__}: {message}'[:1500]
