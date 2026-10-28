"""Run actual MuseTalk v15 inference on the deployment server."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from lipsync_service.engine import digest, probe, validate_inputs
    from lipsync_service.musetalk_engine import MuseTalkEngine
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', required=True, type=Path)
    parser.add_argument('--audio', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--repeat', type=int, default=2, help='Consecutive jobs on the same loaded model')
    parser.add_argument('--timeline', choices=('strict', 'audio'), default='strict',
                        help='strict preserves video; audio matches audio duration and cycles source frames like upstream')
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error('--repeat must be positive')
    output = args.output.resolve()
    if output.suffix.lower() != '.mp4':
        parser.error('--output must be a new .mp4 path')
    outputs = [output.with_name(f'{output.stem}-{i + 1}.mp4') if i else output
               for i in range(args.repeat)]
    if any(path.exists() for path in outputs):
        parser.error('An output already exists; choose a new output name')
    try:
        expected_duration = validate_inputs(args.video, args.audio, args.timeline)
    except ValueError as exc:
        parser.error(f'{exc}. Use --timeline audio to match upstream MuseTalk duration behavior.')
    print(f'Timeline: {args.timeline}; expected output duration: {expected_duration:.3f}s', flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    engine = MuseTalkEngine(model_dir=os.environ.get('LIPSYNC_MODEL_DIR'),
                            gpu=os.environ.get('LIPSYNC_GPU', '0'),
                            batch_size=int(os.environ.get('LIPSYNC_BATCH_SIZE', '1')),
                            timeout=float(os.environ.get('LIPSYNC_INFERENCE_TIMEOUT', '0')),
                            startup_timeout=float(os.environ.get('LIPSYNC_STARTUP_TIMEOUT', '600')),
                            idle_timeout=float(os.environ.get('LIPSYNC_IDLE_TIMEOUT', '600')),
                            media_idle_timeout=float(os.environ.get('LIPSYNC_MEDIA_IDLE_TIMEOUT', '120')),
                            frame_memory_mb=int(os.environ.get('LIPSYNC_FRAME_MEMORY_MB', '256')),
                            use_float16=os.environ.get('LIPSYNC_FLOAT16', '1') == '1', timeline=args.timeline)
    records = []
    try:
        start = time.monotonic()
        engine.start()
        load_seconds = time.monotonic() - start
        for path in outputs:
            start = time.monotonic()
            engine.run(args.video, args.audio, path)
            records.append({'output': str(path), 'seconds': time.monotonic() - start,
                            'video_duration': probe(path, 'video'), 'audio_duration': probe(path, 'audio'),
                            'sha256': digest(path), 'worker_pid': engine._process.pid})
    finally:
        engine.close()
    report = {'model': 'MuseTalk v15', 'timeline': args.timeline, 'expected_duration': expected_duration,
              'load_seconds': load_seconds, 'runs': records,
              'manual_quality_review': 'pending: play outputs and check lip sync, face and audio'}
    output.with_suffix('.verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
