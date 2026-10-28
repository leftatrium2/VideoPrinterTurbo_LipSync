"""MuseTalk v15 inference adapted for aligned, finite video timelines.

Only imported inside the spawned worker. Upstream source is kept unchanged.
"""
import json
import logging
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from .engine import probe
from .audio_stream import AudioFeatures
from .media_stream import VideoFrames, frame_blocks, frame_count, lines, video_writer
from .errors import InferenceError

logger = logging.getLogger(__name__)


def detect_landmarks(paths, preprocessing):
    """Bounded adapter; upstream's summary divides by zero on all-no-face blocks."""
    import cv2
    import numpy as np
    coords, frames = [], []
    for path in paths:
        frame = cv2.imread(path)
        if frame is None:
            raise ValueError('Could not read decoded video frame')
        frames.append(frame)
        detected = preprocessing.fa.get_detections_for_batch(np.asarray([frame]))[0]
        if detected is None:
            coords.append(preprocessing.coord_placeholder)
            continue
        results = preprocessing.merge_data_samples(preprocessing.inference_topdown(preprocessing.model, frame))
        points = results.pred_instances.keypoints
        if not len(points):
            coords.append(detected)
            continue
        face = points[0][23:91].astype(np.int32)
        middle = face[29][1]
        box = (np.min(face[:, 0]), int(max(0, 2 * middle - np.max(face[:, 1]))),
               np.max(face[:, 0]), np.max(face[:, 1]))
        x1, y1, x2, y2 = box
        coords.append(detected if y2 <= y1 or x2 <= x1 or x1 < 0 else box)
    return coords, frames


def normalize_face_box(box, width, height, frame_index):
    """Clip detector coordinates before cropping; never repair an empty box."""
    detail = (f'frame {frame_index} (0-based), box={tuple(box)}, '
              f'image={width}x{height}')
    if len(box) != 4 or not all(math.isfinite(value) for value in box):
        raise ValueError(f'Invalid face bounding box: {detail}')
    x1, y1, x2, y2 = map(int, box)
    x1, x2 = max(0, min(x1, width)), max(0, min(x2, width))
    y1, y2 = max(0, min(y1, height)), max(0, min(y2, height))
    if x1 >= x2 or y1 >= y2:
        raise ValueError(f'Invalid face bounding box after clipping: {detail}')
    return x1, y1, x2, min(y2 + 10, height)  # v15 extra_margin default


def video_metadata(video, *, idle_timeout=120):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
             'stream=avg_frame_rate,width,height,nb_frames:stream_side_data=rotation', '-of', 'json', str(video)],
            check=True, capture_output=True, text=True, timeout=idle_timeout)
    except subprocess.TimeoutExpired:
        raise InferenceError(f'Video metadata probe timed out after {idle_timeout}s') from None
    return json.loads(result.stdout)['streams'][0]


def video_timing(video, *, idle_timeout=120, progress=None, metadata=None):
    info = metadata if metadata is not None else video_metadata(video, idle_timeout=idle_timeout)
    try:
        rate = Fraction(info['avg_frame_rate'])
    except (ValueError, ZeroDivisionError):
        raise ValueError('Video has no valid frame rate') from None
    if rate <= 0:
        raise ValueError('Video has no valid frame rate')
    interval = 1 / float(rate)
    previous, count = None, 0
    command = ['ffprobe', '-v', 'error', '-threads', '2', '-select_streams', 'v:0', '-show_entries',
               'frame=best_effort_timestamp_time', '-of', 'compact=p=0:nk=0', str(video)]
    advertised = info.get('nb_frames', '')
    total = int(advertised) if isinstance(advertised, str) and advertised.isdigit() and int(advertised) > 0 else None
    stream = lines(command, stage='Video timestamp validation', idle_timeout=idle_timeout)
    try:
        for line in stream:
            fields = dict(field.split('=', 1) for field in line.split('|') if '=' in field)
            if 'best_effort_timestamp_time' not in fields:
                continue  # ffprobe may emit a separate side-data line.
            try:
                timestamp = float(fields['best_effort_timestamp_time'])
            except ValueError:
                raise ValueError('Video has no valid frame timestamps') from None
            if not math.isfinite(timestamp):
                raise ValueError('Video has no valid frame timestamps')
            if previous is not None and (timestamp <= previous or
                    abs(timestamp - previous - interval) > min(.002, interval / 10)):
                raise ValueError('MuseTalk requires constant frame rate video; variable frame rate is unsupported')
            previous, count = timestamp, count + 1
            if progress:
                progress({"stage": "timestamp_validation", "completed": count, "total": total, "unit": "frames"})
    finally:
        stream.close()
    if not count:
        raise ValueError('Video has no valid frame timestamps')
    return rate, count


def audio_window_indices(frame_count, fps):
    """Keep fractional FPS; upstream's int(fps) causes drift at 29.97 FPS."""
    step = 50 / float(fps)
    # These are indices into the LEFT-PADDED feature tensor, matching the
    # official two-step look-behind/look-ahead context at integer frame rates.
    return [math.floor(index * step) for index in range(frame_count)]


def output_frame_indices(source_count, fps, audio_duration=None):
    """None keeps the source timeline; audio mode uses upstream's ping-pong cycle."""
    rate = Fraction(str(fps))
    count = source_count if audio_duration is None else math.floor(Fraction(str(audio_duration)) * rate)
    if source_count < 1 or count < 1:
        raise ValueError('Video or audio is too short to generate one frame')
    if audio_duration is not None and abs(float(count / rate) - audio_duration) > .15:
        raise ValueError('Audio duration cannot be represented within 150ms at this video frame rate')
    indices = []
    for index in range(count):
        position = index % (2 * source_count)
        indices.append(position if position < source_count else 2 * source_count - 1 - position)
    return indices


def _is_directory_link(path):
    if path.is_symlink():
        return True
    try:
        tag = getattr(path.lstat(), 'st_reparse_tag', None)
    except FileNotFoundError:
        return False
    # Junctions are not Path.is_symlink() on Python 3.10.
    return tag == getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', 0xA0000003)


def _create_junction(link, target):
    # Constant PowerShell code + environment values avoids cmd.exe expansion of
    # special characters in paths. Junctions do not need Developer Mode/admin.
    env = dict(os.environ, VPT_JUNCTION_PATH=str(link), VPT_JUNCTION_TARGET=str(target))
    subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                    'New-Item -ItemType Junction -Path $env:VPT_JUNCTION_PATH '
                    '-Target $env:VPT_JUNCTION_TARGET -ErrorAction Stop | Out-Null'],
                   env=env, check=True, capture_output=True, timeout=30)


def prepare_workspace(source: Path, models: Path, workspace: Path):
    workspace.mkdir(parents=True, exist_ok=True)
    for name, target in (('musetalk', source / 'musetalk'), ('models', models)):
        link = workspace / name
        if _is_directory_link(link):
            if link.resolve() == target.resolve():
                continue
            if os.name == 'nt' and not link.is_symlink():
                link.rmdir()  # Remove the junction itself, never its target.
            else:
                link.unlink()
        elif link.exists():
            raise RuntimeError(f'Unexpected directory in MuseTalk workspace: {name}')
        if os.name == 'nt':
            _create_junction(link, target)
        else:
            link.symlink_to(target, target_is_directory=True)


def prepare_detector_cache(source, cache, *, use_symlink):
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and os.path.samefile(source, cache):
        return
    if cache.exists() or cache.is_symlink():
        cache.unlink()
    if use_symlink:
        cache.symlink_to(source)
    else:
        try:
            os.link(source, cache)  # No elevation; same-volume NTFS hard link.
        except OSError:
            shutil.copyfile(source, cache)  # Cross-volume model directory.


class MuseTalkRuntime:
    def __init__(self, settings):
        source, models = Path(settings['source']), Path(settings['models'])
        from .source_assets import verify_source
        verify_source(source)
        from .model_assets import verify_models
        verify_models(models)
        detector_cache = Path(os.environ['TORCH_HOME']) / 'hub' / 'checkpoints' / 's3fd-619a316812.pth'
        prepare_detector_cache(models / 's3fd' / 's3fd-619a316812.pth', detector_cache,
                               use_symlink=os.name != 'nt')
        if not (source / 'musetalk' / 'utils' / 'preprocessing.py').is_file():
            raise RuntimeError('Vendored MuseTalk source is missing')
        workspace = Path(settings['runtime']) / 'workspace'
        prepare_workspace(source, models, workspace)
        # All relative upstream paths are isolated to this child, never os.chdir
        # in the HTTP process. Source and model paths above are absolute.
        os.chdir(workspace)
        sys.path[:0] = [str(source), str(source / 'musetalk' / 'utils')]
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('MuseTalk requires an available NVIDIA CUDA device')
        from transformers import WhisperModel
        from musetalk.utils.audio_processor import AudioProcessor
        from musetalk.utils.blending import get_image
        from musetalk.utils.face_parsing import FaceParsing
        from musetalk.utils.preprocessing import coord_placeholder
        from musetalk.utils.utils import load_all_model

        self.torch = torch
        self.device = torch.device('cuda:0')
        self.batch_size = settings['batch_size']
        self.timeline = settings['timeline']
        self.frame_memory_mb = settings.get('frame_memory_mb', 256)
        self.media_idle_timeout = settings.get('media_idle_timeout', 120)
        self.vae, self.unet, self.pe = load_all_model(
            unet_model_path=str(models / 'musetalkV15' / 'unet.pth'),
            unet_config=str(models / 'musetalkV15' / 'musetalk.json'),
            vae_type='sd-vae', device=self.device)
        self.dtype = torch.float16 if settings['float16'] else torch.float32
        for module in (self.vae.vae, self.unet.model, self.pe):
            module.to(device=self.device, dtype=self.dtype).eval()
            module.requires_grad_(False)
        self.audio_processor = AudioProcessor(feature_extractor_path=str(models / 'whisper'))
        self.whisper = WhisperModel.from_pretrained(str(models / 'whisper'), local_files_only=True)
        self.whisper.to(device=self.device, dtype=self.dtype).eval().requires_grad_(False)
        self.fp = FaceParsing(left_cheek_width=90, right_cheek_width=90)
        self.get_image = get_image
        from musetalk.utils import preprocessing
        self.get_landmarks = lambda paths, shift: detect_landmarks(paths, preprocessing)
        self.no_face = coord_placeholder
        self.timesteps = torch.tensor([0], device=self.device)

    def _process_block(self, paths, source_indices, output_start, rate, audio_features, write, report):
        import cv2
        import numpy as np
        torch = self.torch
        coords, frames = self.get_landmarks(list(map(str, paths)), 0)
        if len(coords) != len(paths) or len(frames) != len(paths):
            raise ValueError('Face preprocessing frame count mismatch')
        boxes = []
        for box, frame, source_index in zip(coords, frames, source_indices):
            boxes.append(None if tuple(box) == self.no_face else
                         normalize_face_box(box, frame.shape[1], frame.shape[0], source_index))
        report(f'Face detection finished at output frame {output_start}')
        for start in range(0, len(paths), self.batch_size):
            end = min(start + self.batch_size, len(paths))
            active = [i for i in range(start, end) if boxes[i] is not None]
            combined = list(frames[start:end])
            if active:
                chunks = audio_features.features([output_start + i for i in active], rate)
                latents = []
                for index in active:
                    x1, y1, x2, y2 = boxes[index]
                    crop = cv2.resize(frames[index][y1:y2, x1:x2], (256, 256),
                                      interpolation=cv2.INTER_LANCZOS4)
                    latents.append(self.vae.get_latents_for_unet(crop))
                prediction = self.unet.model(
                    torch.cat(latents).to(dtype=self.dtype), self.timesteps,
                    encoder_hidden_states=self.pe(chunks)).sample
                reconstructed = self.vae.decode_latents(prediction)
                if len(reconstructed) != len(active):
                    raise ValueError('Model output frame count mismatch')
                for index, face in zip(active, reconstructed):
                    x1, y1, x2, y2 = boxes[index]
                    face = cv2.resize(face.astype(np.uint8), (x2 - x1, y2 - y1))
                    combined[index - start] = self.get_image(frames[index].copy(), face, boxes[index],
                                                            mode='jaw', fp=self.fp)
            for frame in combined:
                write(frame)
            report({'stage': 'inference', 'completed': output_start + end, 'unit': 'frames'})

    def run(self, video, audio, output, *, timeline=None):
        torch = self.torch
        selected = getattr(self, 'timeline', 'strict') if timeline is None else timeline
        if selected not in ('strict', 'audio'):
            raise ValueError('Timeline must be strict or audio')
        last_report = 0.
        current = {'stage': 'timestamp_validation'}
        output_count = None

        def report(update):
            nonlocal last_report, current
            changed = isinstance(update, dict) and update.get('stage') != current.get('stage')
            if isinstance(update, dict):
                current = dict(update)
                if current.get('stage') == 'inference':
                    current['total'] = output_count
            now = time.monotonic()
            if changed or now - last_report >= 1 or (isinstance(update, dict) and update.get('stage') == 'inference'):
                callback = getattr(self, 'progress', None)
                if callback:
                    callback(dict(current))
                if now - last_report >= 1:
                    logger.info('MuseTalk progress: %s', update)
                last_report = now

        idle = getattr(self, 'media_idle_timeout', 120)
        metadata = video_metadata(video, idle_timeout=idle)
        width, height = int(metadata['width']), int(metadata['height'])
        rotation = next((float(item['rotation']) for item in metadata.get('side_data_list', [])
                         if 'rotation' in item), 0)
        if rotation % 90:
            raise ValueError('MuseTalk supports video rotations in multiples of 90 degrees')
        if rotation % 180:
            width, height = height, width
        if min(width, height) < 1:
            raise ValueError('Video has invalid dimensions')
        rate, source_count = video_timing(video, idle_timeout=idle, progress=report, metadata=metadata)
        audio_duration = probe(audio, 'audio') if selected == 'audio' else None
        output_count = frame_count(source_count, rate, audio_duration)
        report({'stage': 'inference', 'completed': 0, 'total': output_count, 'unit': 'frames'})
        # Reserve 3x pixel storage for arrays, copies and pipe buffers. Decoder,
        # encoder and model workspaces are additional, resolution-dependent costs.
        budget = getattr(self, 'frame_memory_mb', 256) * 1024**2
        limit = max(1, min(32, int(budget // (width * height * 3 * 3))))
        logger.info('Streaming %sx%s at %s FPS: %s output frames, block size %s',
                    width, height, rate, output_count, limit)
        with tempfile.TemporaryDirectory(prefix='musetalk-', dir=output.parent) as directory:
            directory = Path(directory)
            audio_features = AudioFeatures(self, audio, directory, idle_timeout=idle, progress=report)
            decoder = VideoFrames(video, width, height, rate, source_count,
                                  idle_timeout=idle, progress=report)
            try:
                with torch.inference_mode(), video_writer(output, width, height, rate,
                                                          idle_timeout=idle, progress=report) as write:
                    for output_start, source_indices in frame_blocks(source_count, output_count, limit):
                        report(f'Video block starting at output frame {output_start}/{output_count}')
                        with tempfile.TemporaryDirectory(prefix='frames-', dir=directory) as block:
                            paths = decoder.block(source_indices, Path(block))
                            self._process_block(paths, source_indices, output_start, rate,
                                                audio_features, write, report)
                    report({'stage': 'encoding_finalize'})
            finally:
                decoder.close()
                audio_features.close()
