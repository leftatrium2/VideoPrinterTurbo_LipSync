"""No-face blocks must bypass pose inference and upstream's divide-by-zero summary."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lipsync_service.musetalk_runtime import detect_landmarks
from media_fixtures import load_cv2


def test_all_missing_faces_skip_pose_inference(tmp_path, monkeypatch):
    np = pytest.importorskip('numpy')
    cv2 = load_cv2(monkeypatch)
    path = tmp_path / 'frame.png'
    cv2.imwrite(str(path), np.zeros((400, 400, 3), dtype=np.uint8))
    preprocessing = SimpleNamespace(fa=SimpleNamespace(get_detections_for_batch=Mock(return_value=[None])),
                                    coord_placeholder=(0., 0., 0., 0.), inference_topdown=Mock())
    coords, frames = detect_landmarks([str(path)] * 3, preprocessing)
    assert coords == [preprocessing.coord_placeholder] * 3
    assert len(frames) == 3
    preprocessing.inference_topdown.assert_not_called()


def test_mixed_missing_and_present_faces_preserve_coordinate_order(tmp_path, monkeypatch):
    np = pytest.importorskip('numpy')
    cv2 = load_cv2(monkeypatch)
    path = tmp_path / 'frame.png'
    cv2.imwrite(str(path), np.zeros((400, 400, 3), dtype=np.uint8))
    points = np.zeros((1, 91, 2))
    points[0, 23:91, 0] = np.arange(100, 168)
    points[0, 23:91, 1] = np.arange(200, 268)
    preprocessing = SimpleNamespace(
        fa=SimpleNamespace(get_detections_for_batch=Mock(side_effect=[[None], [(10, 10, 200, 200)]])),
        coord_placeholder=(0., 0., 0., 0.), model=object(), inference_topdown=Mock(return_value='pose'),
        merge_data_samples=Mock(return_value=SimpleNamespace(pred_instances=SimpleNamespace(keypoints=points))))
    coords, frames = detect_landmarks([str(path)] * 2, preprocessing)
    assert coords == [preprocessing.coord_placeholder, (100, 191, 167, 267)]
    assert len(frames) == 2
    preprocessing.inference_topdown.assert_called_once()
