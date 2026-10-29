"""Full-resolution memory frames, proxy coordinate mapping and batched VAE contracts."""
import logging
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lipsync_service.musetalk_runtime import batched_vae_latents, detect_landmarks
from lipsync_service.profiling import StageProfiler
from media_fixtures import load_cv2


def preprocessor(points, detections):
    return SimpleNamespace(
        fa=SimpleNamespace(get_detections_for_batch=Mock(side_effect=detections)),
        coord_placeholder=(0., 0., 0., 0.), model=object(), inference_topdown=Mock(return_value='pose'),
        merge_data_samples=Mock(return_value=SimpleNamespace(pred_instances=SimpleNamespace(keypoints=points))))


def test_proxy_detection_maps_landmarks_to_full_resolution(monkeypatch):
    np = pytest.importorskip('numpy')
    cv2 = load_cv2(monkeypatch)
    # The Pillow adapter is only a CPU test shim; INTER_AREA selects the proxy path.
    monkeypatch.setattr(cv2, 'INTER_AREA', getattr(cv2, 'INTER_AREA', 3), raising=False)
    frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
    points = np.zeros((1, 91, 2))
    points[0, 23:91, 0] = np.arange(100, 168)
    points[0, 23:91, 1] = np.arange(200, 268)
    module = preprocessor(points, [[(80, 100, 200, 300)]])
    coords, frames = detect_landmarks([frame], module, max_side=1280)
    assert frames[0] is frame
    assert frames[0].shape == (2160, 3840, 3)
    assert module.fa.get_detections_for_batch.call_args.args[0].shape == (1, 720, 1280, 3)
    assert module.inference_topdown.call_args.args[1].shape == (720, 1280, 3)
    assert coords == [(300, 573, 501, 801)]


def test_small_face_proxy_miss_retries_full_resolution_and_zero_disables_resize(monkeypatch):
    np = pytest.importorskip('numpy')
    cv2 = load_cv2(monkeypatch)
    monkeypatch.setattr(cv2, 'INTER_AREA', getattr(cv2, 'INTER_AREA', 3), raising=False)
    frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
    # Empty pose result uses the detector box in the actual detection image's coordinates.
    module = preprocessor(np.zeros((0, 91, 2)), [[None], [(20, 30, 200, 300)]])
    coords, _ = detect_landmarks([frame], module, max_side=1280)
    assert coords == [(20, 30, 200, 300)]
    calls = module.fa.get_detections_for_batch.call_args_list
    assert [call.args[0].shape for call in calls] == [(1, 720, 1280, 3), (1, 2160, 3840, 3)]
    module = preprocessor(np.zeros((0, 91, 2)), [[(20, 30, 200, 300)]])
    detect_landmarks([frame], module, max_side=0)
    assert module.fa.get_detections_for_batch.call_count == 1
    assert module.inference_topdown.call_args.args[1] is frame


def test_proxy_box_fallback_uses_both_actual_resize_ratios(monkeypatch):
    np = pytest.importorskip('numpy')
    cv2 = load_cv2(monkeypatch)
    monkeypatch.setattr(cv2, 'INTER_AREA', getattr(cv2, 'INTER_AREA', 3), raising=False)
    frame = np.zeros((1001, 2001, 3), dtype=np.uint8)
    module = preprocessor(np.zeros((0, 91, 2)), [[(10, 20, 100, 200)]])
    coords, _ = detect_landmarks([frame], module, max_side=1280)
    resized = module.inference_topdown.call_args.args[1]
    sx, sy = 2001 / resized.shape[1], 1001 / resized.shape[0]
    assert coords[0] == (10 * sx, 20 * sy, 100 * sx, 200 * sy)


def test_vae_batching_preserves_frame_order_mask_and_channel_order():
    torch = pytest.importorskip('torch')
    def preprocess(crop, half_mask):
        image = torch.full((1, 3, 4, 4), float(crop))
        if half_mask:
            image[:, :, 2:, :] = 0
        return image * 2 - 1
    encoded = []
    def encode(batch):
        encoded.append(batch.clone())
        return batch[:, :2] * 3
    vae = SimpleNamespace(preprocess_img=preprocess, encode_latents=encode)
    actual = batched_vae_latents(vae, [1, 2, 3], torch)
    assert len(encoded) == 2  # two batched forwards, rather than six forwards
    reference = torch.cat([torch.cat([preprocess(i, True)[:, :2] * 3,
                                     preprocess(i, False)[:, :2] * 3], dim=1) for i in [1, 2, 3]])
    assert torch.equal(actual, reference)


def test_profiler_attributes_gpu_work_and_reports_processing_fps(monkeypatch, caplog):
    import lipsync_service.profiling as module
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(module.time, 'perf_counter', lambda: clock.now)
    cuda = SimpleNamespace(is_available=lambda: True, synchronize=Mock(), memory_allocated=lambda: 1024**2)
    profile = StageProfiler(SimpleNamespace(cuda=cuda))
    clock.now = 1
    profile.start_frames()
    with caplog.at_level(logging.INFO):
        clock.now = 3
        with profile.measure('unet', gpu=True):
            clock.now = 5
        clock.now = 9
        profile.report(2, 10, force=True)
    assert cuda.synchronize.call_count == 2
    assert 'unet=2.000s' in caplog.text
    assert 'processing_fps=0.250' in caplog.text
    assert 'gpu_synchronized=True' in caplog.text
    assert 'gpu_allocated=1MiB' in caplog.text
