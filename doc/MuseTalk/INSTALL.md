# MuseTalk 1.5 Installation and Verification

This guide describes how to install MuseTalk 1.5 from source on Ubuntu with an NVIDIA RTX 3080, using Python 3.10 and a Conda environment named `musetalk`, without Docker.

The installation directory used in this guide is `~/apps/MuseTalk`. Before proceeding, install Conda and the NVIDIA driver, and confirm that `nvidia-smi` runs successfully.

These steps are based on the official installation instructions; they are not a complete lockfile for the previously working environment. If additional fixes were applied during that installation, use the version records exported from the working environment as the reference.

## 1. Clone the Repository

```bash
mkdir -p ~/apps
cd ~/apps

git clone https://github.com/TMElyralab/MuseTalk.git
cd MuseTalk
```

## 2. Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg libgl1 libglib2.0-0 build-essential

ffmpeg -version
ffprobe -version
nvidia-smi
```

## 3. Create the Python Environment

```bash
conda create -n musetalk python=3.10 -y
conda activate musetalk

python -m pip install --upgrade pip wheel
```

Unless otherwise stated, run all subsequent commands in `~/apps/MuseTalk` with the `musetalk` environment activated.

## 4. Install PyTorch

Install PyTorch 2.0.1 using the CUDA 11.8 wheels:

```bash
python -m pip install \
  torch==2.0.1 \
  torchvision==0.15.2 \
  torchaudio==2.0.2 \
  --index-url https://download.pytorch.org/whl/cu118
```

Check GPU availability:

```bash
python -c "import torch; print('torch:', torch.__version__); print('CUDA:', torch.version.cuda); print('available:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
```

The output should show `available: True` and the GPU name.

## 5. Install Project Dependencies

```bash
python -m pip install -r requirements.txt
```

The official requirements file specifies versions for libraries such as NumPy, Diffusers, and Transformers. Avoid upgrading all of these dependencies indiscriminately, as this may break compatibility.

## 6. Install OpenMMLab Dependencies

Install the packages in the following order:

```bash
python -m pip install --no-cache-dir -U openmim

mim install mmengine
mim install "mmcv==2.0.1"
mim install "mmdet==3.1.0"
mim install "mmpose==1.1.0"
```

Verify that the core modules and MMCV operators can be imported:

```bash
python - <<'PY'
import torch
import mmcv
import mmengine
import mmdet
import mmpose
from mmcv.ops import nms

print("torch:", torch.__version__)
print("mmcv:", mmcv.__version__)
print("mmengine:", mmengine.__version__)
print("mmdet:", mmdet.__version__)
print("mmpose:", mmpose.__version__)
print("MMCV ops imported successfully")
PY
```

## 7. Download the Models

Run the official model download script:

```bash
bash ./download_weights.sh
```

Check the main model directories:

```bash
ls -lh models/musetalkV15/
ls -lh models/whisper/
ls -lh models/sd-vae/
ls -lh models/dwpose/
ls -lh models/face-parse-bisent/
```

The main MuseTalk 1.5 model directory should contain:

```text
models/musetalkV15/musetalk.json
models/musetalkV15/unet.pth
```

The main model alone is insufficient for inference. The weights for the other components provided by the download script are also required.

## 8. Prepare Test Inputs and Configuration

First, create the directories:

```bash
mkdir -p data/local_test configs/inference
```

Place the test video and voice audio at the following locations:

```text
~/apps/MuseTalk/data/local_test/speaker.mp4
~/apps/MuseTalk/data/local_test/voice.wav
```

Create the configuration file. The following command overwrites the file if it already exists:

```bash
cat > configs/inference/local_test.yaml <<'YAML'
local_test:
  video_path: data/local_test/speaker.mp4
  audio_path: data/local_test/voice.wav
  result_name: speaker_synced.mp4
YAML
```

This configuration recreates a test task for the same purpose. Adjust the input paths to match the actual file locations.

## 9. Run the MuseTalk 1.5 Verification

Use the previously tested settings: v15, FP16, and batch_size=1:

```bash
CUDA_VISIBLE_DEVICES=0 python -m scripts.inference \
  --inference_config configs/inference/local_test.yaml \
  --result_dir results/local_test \
  --unet_model_path models/musetalkV15/unet.pth \
  --unet_config models/musetalkV15/musetalk.json \
  --whisper_dir models/whisper \
  --version v15 \
  --batch_size 1 \
  --use_float16 \
  --ffmpeg_path /usr/bin
```

`--ffmpeg_path` specifies the directory containing FFmpeg. Here it corresponds to `/usr/bin/ffmpeg`, installed through apt.

Expected output:

```text
~/apps/MuseTalk/results/local_test/v15/speaker_synced.mp4
```

Play the generated file and check the lip synchronization, audio track, and video quality. Successful dependency imports do not constitute acceptance of the inference result.

## 10. Record the Working Environment Versions

Run the following on the machine where inference has succeeded:

```bash
conda activate musetalk
cd ~/apps/MuseTalk

python -m pip freeze > requirements.working.txt
conda env export > environment.working.yml
git rev-parse HEAD > musetalk.commit.txt
```

| File | Contents |
| --- | --- |
| `requirements.working.txt` | Installed Python dependency versions |
| `environment.working.yml` | Exported Conda environment information |
| `musetalk.commit.txt` | Current MuseTalk source commit hash |

Use the working environment as the baseline for subsequent integration with VideoPrinterTurbo_LipSync. Cloning the current `main` branch again does not guarantee identical source code and dependencies. These records help reproduce the environment; they are not a validated installation package for other machines.

## Official References

- [MuseTalk repository and installation instructions](https://github.com/TMElyralab/MuseTalk)
- [Python requirements file](https://github.com/TMElyralab/MuseTalk/blob/main/requirements.txt)
- [Model download script](https://github.com/TMElyralab/MuseTalk/blob/main/download_weights.sh)
