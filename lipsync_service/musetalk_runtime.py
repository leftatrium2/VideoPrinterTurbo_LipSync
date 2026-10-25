"""MuseTalk v15 inference adapted for aligned, finite video timelines.

Only imported inside the spawned worker. Upstream source is kept unchanged.
"""
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

from .engine import ffmpeg, probe


def video_timing(video):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
         'stream=avg_frame_rate:frame=best_effort_timestamp_time', '-of', 'json', str(video)],
        check=True, capture_output=True, text=True, timeout=120)
    info = json.loads(result.stdout)
    rate = Fraction(info['streams'][0]['avg_frame_rate'])
    if rate <= 0:
        raise ValueError('Video has no valid frame rate')
    timestamps = [float(frame['best_effort_timestamp_time']) for frame in info['frames']]
    if not timestamps or any(not math.isfinite(t) for t in timestamps):
        raise ValueError('Video has no valid frame timestamps')
    interval = 1 / float(rate)
    if any(b <= a or abs(b - a - interval) > min(.002, interval / 10)
           for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError('MuseTalk requires constant frame rate video; variable frame rate is unsupported')
    return rate, len(timestamps)


def audio_window_indices(frame_count, fps):
    """Keep fractional FPS; upstream's int(fps) causes drift at 29.97 FPS."""
    step = 50 / float(fps)
    # These are indices into the LEFT-PADDED feature tensor, matching the
    # official two-step look-behind/look-ahead context at integer frame rates.
    return [math.floor(index * step) for index in range(frame_count)]


def output_frame_indices(source_count, fps, audio_duration=None):
    """None keeps the source timeline; audio mode uses upstream's ping-pong cycle."""
    count = source_count if audio_duration is None else math.floor(audio_duration * fps)
    if source_count < 1 or count < 1:
        raise ValueError('Video or audio is too short to generate one frame')
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
        from musetalk.utils.preprocessing import get_landmark_and_bbox, coord_placeholder
        from musetalk.utils.utils import load_all_model

        self.torch = torch
        self.device = torch.device('cuda:0')
        self.batch_size = settings['batch_size']
        self.timeline = settings['timeline']
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
        self.get_landmarks = get_landmark_and_bbox
        self.no_face = coord_placeholder
        self.timesteps = torch.tensor([0], device=self.device)

    def _audio_features(self, audio, frame_count, fps):
        torch = self.torch
        features, length = self.audio_processor.get_audio_feature(str(audio))
        encoded = [torch.stack(self.whisper.encoder(
            feature.to(device=self.device, dtype=self.dtype),
            output_hidden_states=True).hidden_states, dim=2) for feature in features]
        encoded = torch.cat(encoded, dim=1)[:, :math.floor(length / 16000 * 50)]
        if not encoded.shape[1]:
            raise ValueError('Audio is too short for MuseTalk')
        indices = audio_window_indices(frame_count, fps)
        left = math.ceil(50 / float(fps)) * 2
        window = 10  # official left/right padding 2, two audio tokens per step
        right = max(window, indices[-1] + window - left - encoded.shape[1])
        padded = torch.cat((encoded.new_zeros((1, left, *encoded.shape[2:])), encoded,
                            encoded.new_zeros((1, right, *encoded.shape[2:]))), dim=1)
        chunks = torch.cat([padded[:, i:i + window] for i in indices], dim=0)
        return chunks.reshape(frame_count, -1, chunks.shape[-1])

    def run(self, video, audio, output):
        import cv2
        import numpy as np
        torch = self.torch
        rate, expected_frames = video_timing(video)
        audio_duration = probe(audio, 'audio') if getattr(self, 'timeline', 'strict') == 'audio' else None
        frame_indices = output_frame_indices(expected_frames, rate, audio_duration)
        output_count = len(frame_indices)
        with tempfile.TemporaryDirectory(prefix='musetalk-', dir=output.parent) as temp:
            temp = Path(temp)
            frames_dir, result_dir = temp / 'frames', temp / 'generated'
            frames_dir.mkdir()
            result_dir.mkdir()
            ffmpeg(['-i', video, '-map', '0:v:0', '-vsync', '0', '-start_number', '0',
                    frames_dir / '%08d.png'])
            paths = sorted(frames_dir.glob('*.png'))
            if len(paths) != expected_frames:
                raise ValueError('Decoded video frame count mismatch')
            with torch.inference_mode():
                coords, frames = self.get_landmarks(list(map(str, paths)), 0)
                if len(coords) != len(paths) or len(frames) != len(paths):
                    raise ValueError('Face preprocessing frame count mismatch')
                boxes = []
                for box, frame in zip(coords, frames):
                    if tuple(box) == self.no_face:
                        raise ValueError('A video frame has no detectable face')
                    x1, y1, x2, y2 = map(int, box)
                    y2 = min(y2 + 10, frame.shape[0])  # v15 extra_margin default
                    if not (0 <= x1 < x2 <= frame.shape[1] and 0 <= y1 < y2 <= frame.shape[0]):
                        raise ValueError('Invalid face bounding box')
                    boxes.append((x1, y1, x2, y2))
                chunks = self._audio_features(audio, output_count, rate)
                for start in range(0, output_count, self.batch_size):
                    end = min(start + self.batch_size, output_count)
                    latents = []
                    for index in range(start, end):
                        source_index = frame_indices[index]
                        x1, y1, x2, y2 = boxes[source_index]
                        crop = cv2.resize(frames[source_index][y1:y2, x1:x2], (256, 256),
                                          interpolation=cv2.INTER_LANCZOS4)
                        latents.append(self.vae.get_latents_for_unet(crop))
                    prediction = self.unet.model(
                        torch.cat(latents).to(dtype=self.dtype), self.timesteps,
                        encoder_hidden_states=self.pe(chunks[start:end])).sample
                    reconstructed = self.vae.decode_latents(prediction)
                    if len(reconstructed) != end - start:
                        raise ValueError('Model output frame count mismatch')
                    for index, face in zip(range(start, end), reconstructed):
                        source_index = frame_indices[index]
                        x1, y1, x2, y2 = boxes[source_index]
                        face = cv2.resize(face.astype(np.uint8), (x2 - x1, y2 - y1))
                        combined = self.get_image(frames[source_index].copy(), face, boxes[source_index],
                                                  mode='jaw', fp=self.fp)
                        if not cv2.imwrite(str(result_dir / f'{index:08d}.png'), combined):
                            raise RuntimeError('Could not write generated frame')
            ffmpeg(['-framerate', str(rate), '-start_number', '0', '-i', result_dir / '%08d.png',
                    '-frames:v', str(output_count), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-crf', '18', output])
