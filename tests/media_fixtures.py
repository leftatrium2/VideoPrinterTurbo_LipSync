"""Pillow fallback for CPU tests that mock MuseTalk and do not need OpenCV kernels."""
from types import SimpleNamespace


def load_cv2(monkeypatch):
    try:
        import cv2
        return cv2
    except ImportError:
        import numpy as np
        from PIL import Image
        def read(path):
            with Image.open(path) as image:
                return np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
        def write(path, frame):
            Image.fromarray(frame[:, :, ::-1]).save(path)
            return True
        def resize(frame, size, **kwargs):
            return np.asarray(Image.fromarray(frame).resize(size, Image.Resampling.LANCZOS)).copy()
        cv2 = SimpleNamespace(imread=read, imwrite=write, resize=resize, INTER_LANCZOS4=4)
        monkeypatch.setitem(__import__('sys').modules, 'cv2', cv2)
        return cv2
