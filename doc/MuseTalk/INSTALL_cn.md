# MuseTalk 1.5 安装与验证

本文整理 Ubuntu + NVIDIA RTX 3080 环境下的 MuseTalk 1.5 源码安装步骤，使用 Python 3.10 和名为 `musetalk` 的 Conda 环境，不使用 Docker。

安装目录约定为 `~/apps/MuseTalk`。执行前应已安装 Conda 和 NVIDIA 驱动，且 `nvidia-smi` 可以正常运行。

这些步骤根据官方安装说明整理，不是此前成功环境的完整锁定清单。上次安装过程中若有额外修复，应以成功环境导出的版本记录为准。

## 1. 克隆项目

```bash
mkdir -p ~/apps
cd ~/apps

git clone https://github.com/TMElyralab/MuseTalk.git
cd MuseTalk
```

## 2. 安装系统依赖

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg libgl1 libglib2.0-0 build-essential

ffmpeg -version
ffprobe -version
nvidia-smi
```

## 3. 创建 Python 环境

```bash
conda create -n musetalk python=3.10 -y
conda activate musetalk

python -m pip install --upgrade pip wheel
```

后续命令均在已激活的 `musetalk` 环境、`~/apps/MuseTalk` 目录中执行，除非另有说明。

## 4. 安装 PyTorch

使用 PyTorch 2.0.1 和 CUDA 11.8 wheel：

```bash
python -m pip install \
  torch==2.0.1 \
  torchvision==0.15.2 \
  torchaudio==2.0.2 \
  --index-url https://download.pytorch.org/whl/cu118
```

检查 GPU 是否可用：

```bash
python -c "import torch; print('torch:', torch.__version__); print('CUDA:', torch.version.cuda); print('available:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
```

其中 `available` 应为 `True`，并显示 GPU 名称。

## 5. 安装项目依赖

```bash
python -m pip install -r requirements.txt
```

官方依赖文件对 NumPy、Diffusers、Transformers 等库指定了版本。不要直接统一升级这些依赖，以免破坏兼容性。

## 6. 安装 OpenMMLab 依赖

按以下顺序安装：

```bash
python -m pip install --no-cache-dir -U openmim

mim install mmengine
mim install "mmcv==2.0.1"
mim install "mmdet==3.1.0"
mim install "mmpose==1.1.0"
```

验证核心模块及 MMCV 算子能正常导入：

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

## 7. 下载模型

执行官方模型下载脚本：

```bash
bash ./download_weights.sh
```

检查关键模型目录：

```bash
ls -lh models/musetalkV15/
ls -lh models/whisper/
ls -lh models/sd-vae/
ls -lh models/dwpose/
ls -lh models/face-parse-bisent/
```

MuseTalk 1.5 主模型应包含：

```text
models/musetalkV15/musetalk.json
models/musetalkV15/unet.pth
```

仅有主模型不足以推理，还需要下载脚本准备的其他组件权重。

## 8. 准备测试素材与配置

先创建目录：

```bash
mkdir -p data/local_test configs/inference
```

将测试视频和配音分别放到以下位置：

```text
~/apps/MuseTalk/data/local_test/speaker.mp4
~/apps/MuseTalk/data/local_test/voice.wav
```

创建配置文件；如果该文件已存在，以下命令会覆盖它：

```bash
cat > configs/inference/local_test.yaml <<'YAML'
local_test:
  video_path: data/local_test/speaker.mp4
  audio_path: data/local_test/voice.wav
  result_name: speaker_synced.mp4
YAML
```

该配置重建了相同用途的测试任务，素材路径可按实际位置修改。

## 9. 执行 MuseTalk 1.5 验证

采用此前验证使用的 v15、FP16、batch_size=1 参数：

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

`--ffmpeg_path` 指向 FFmpeg 所在目录，此处对应通过 apt 安装的 `/usr/bin/ffmpeg`。

预期输出：

```text
~/apps/MuseTalk/results/local_test/v15/speaker_synced.mp4
```

生成文件后应实际播放，检查口型同步效果、音轨和画面是否正常。依赖导入通过并不代表推理结果已经验收。

## 10. 保存成功环境的版本记录

在已经跑通的机器上执行：

```bash
conda activate musetalk
cd ~/apps/MuseTalk

python -m pip freeze > requirements.working.txt
conda env export > environment.working.yml
git rev-parse HEAD > musetalk.commit.txt
```

| 文件 | 内容 |
| --- | --- |
| `requirements.working.txt` | 当前 Python 依赖版本 |
| `environment.working.yml` | 当前 Conda 环境导出信息 |
| `musetalk.commit.txt` | 当前 MuseTalk 源码提交号 |

后续与 VideoPrinterTurbo_LipSync 集成时，应以成功环境为基准。重新克隆当前 `main` 分支并不保证得到相同源码和依赖；这些记录用于辅助复现，不等同于经过验证的跨机器安装包。

## 官方参考

- [MuseTalk 仓库与安装说明](https://github.com/TMElyralab/MuseTalk)
- [Python 依赖文件](https://github.com/TMElyralab/MuseTalk/blob/main/requirements.txt)
- [模型下载脚本](https://github.com/TMElyralab/MuseTalk/blob/main/download_weights.sh)
