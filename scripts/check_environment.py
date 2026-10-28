"""Deployment preflight; --load-model tests CUDA initialization without a video."""
import argparse
import json
import os
import shutil
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def print_worker_log(log_path, lines=80):
    print(f'MuseTalk worker log (last {lines} lines): {log_path}', file=sys.stderr)
    try:
        with Path(log_path).open(encoding='utf-8', errors='replace') as log:
            tail = deque(log, maxlen=lines)
        for line in tail:
            print(line, end='', file=sys.stderr)
    except OSError as exc:
        print(f'Worker log unavailable: {exc}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--load-model', action='store_true')
    args = parser.parse_args()
    from lipsync_service.source_assets import verify_source
    print(f'MuseTalk bundled source verified ({verify_source()} files).')
    if sys.version_info[:2] != (3, 10):
        raise RuntimeError('Deployment requires Python 3.10')
    for executable in ('ffmpeg', 'ffprobe', 'nvidia-smi'):
        if not shutil.which(executable):
            raise RuntimeError(f'Missing executable: {executable}')
    os.environ['CUDA_VISIBLE_DEVICES'] = os.environ.get('LIPSYNC_GPU', '0')
    import torch
    import mmcv
    import mmengine
    import mmdet
    import mmpose
    from mmcv.ops import nms
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is unavailable; check NVIDIA driver and PyTorch cu118 installation')
    boxes = torch.tensor([[0., 0., 10., 10.]], device='cuda')
    nms(boxes, torch.ones(1, device='cuda'), .5)
    info = {'python': sys.version, 'torch': torch.__version__, 'cuda': torch.version.cuda,
            'gpu': torch.cuda.get_device_name(0), 'mmcv': mmcv.__version__,
            'mmengine': mmengine.__version__, 'mmdet': mmdet.__version__, 'mmpose': mmpose.__version__}
    records = ROOT / 'deployment-records'
    records.mkdir(exist_ok=True)
    (records / 'environment-check.json').write_text(json.dumps(info, indent=2) + '\n')
    print(json.dumps(info, indent=2))
    if args.load_model:
        from lipsync_service.musetalk_engine import MuseTalkEngine
        engine = MuseTalkEngine(model_dir=os.environ.get('LIPSYNC_MODEL_DIR'),
                                gpu=os.environ.get('LIPSYNC_GPU', '0'),
                                batch_size=int(os.environ.get('LIPSYNC_BATCH_SIZE', '1')),
                                timeout=float(os.environ.get('LIPSYNC_INFERENCE_TIMEOUT', '0')),
                                startup_timeout=float(os.environ.get('LIPSYNC_STARTUP_TIMEOUT', '600')),
                                idle_timeout=float(os.environ.get('LIPSYNC_IDLE_TIMEOUT', '600')),
                                media_idle_timeout=float(os.environ.get('LIPSYNC_MEDIA_IDLE_TIMEOUT', '120')),
                                frame_memory_mb=int(os.environ.get('LIPSYNC_FRAME_MEMORY_MB', '256')),
                                use_float16=os.environ.get('LIPSYNC_FLOAT16', '1') == '1')
        try:
            try:
                engine.start()
            except Exception:
                print_worker_log(Path(engine.settings['runtime']) / 'worker.log')
                raise
            print('MuseTalk v15 initialized successfully. Video quality still requires a real inference test.')
        finally:
            engine.close()


if __name__ == '__main__':
    main()
