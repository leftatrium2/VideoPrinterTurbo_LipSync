# VideoPrinterTurbo_LipSync

[English](README.md) | 简体中文

供 VideoPrinterTurbo 调用的本地口型同步后端，使用内置 MuseTalk 1.5（`v15`）、FastAPI 和 Uvicorn，无前台界面。用户只部署本项目，无需另行克隆 MuseTalk 或配置推理命令。

部署目标为 **Ubuntu x86_64 或原生 Windows 10/11 x64 + NVIDIA GPU + Python 3.10**。Windows 使用原生 PowerShell/批处理入口。本地协议和进程测试已实现；干净系统安装、真实 GPU 推理及口型质量仍需在目标机器验收。内置源码尚未与原服务器成功环境核对，详见 [实施清单](doc/MuseTalk/INTEGRATION_TODO.md)。

## 安装与启动

### Windows

前提：64 位 Windows 10/11 和 NVIDIA 驱动，`nvidia-smi.exe` 可用。将项目解压到较短的英文路径，例如 `D:\VideoPrinterTurbo_LipSync`，避免 Conda/OpenCV 对中文目录和长路径的兼容问题。

1. 双击 `install.bat`，等待安装和模型初始化完成。
2. 双击 `start.bat`，保持窗口打开。
3. 打开 <http://127.0.0.1:8081/docs>，或访问 `/health` 检查就绪状态。

也可以在项目目录执行：

```powershell
.\install.bat
.\start.bat
```

脚本复用可用的 Conda；找不到时自动下载固定版本、SHA-256 校验的 Miniforge 到项目 `.tools/miniforge`，不注册为默认 Python。创建项目内 `.conda/musetalk` Python 3.10 环境，通过 conda-forge 安装 FFmpeg 和 VC++ 运行库，并安装 PyTorch cu118、MMCV 和 xtcocotools 的 Windows 预编译包。无需安装编译工具链或单独配置系统 FFmpeg。每一步失败都会停止，不会继续输出安装成功。

Windows 的模型资源使用目录联接，S3FD 缓存使用硬链接或复制，无需开启开发者模式来创建符号链接。推理进程由 Windows Job Object 管理，用于回收推理和 FFmpeg 子进程。`.bat` 启动器只对当前 PowerShell 进程设置脚本执行策略。

可在同一个 PowerShell 窗口设置镜像、代理或服务配置，然后执行脚本：

```powershell
$env:HF_ENDPOINT = 'https://hf-mirror.com'
# 可选，地址必须在当前 Windows 机器上可用
# $env:HTTPS_PROXY = 'http://127.0.0.1:7890'
.\install.bat
$env:LIPSYNC_HOST = '0.0.0.0'
$env:LIPSYNC_API_KEY = 'replace-with-your-own-key'
.\start.bat
```

也可运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`；启动时替换为 `start.ps1`。Windows 安装、DLL 加载、目录联接、Job Object 和真实 GPU 推理仍需在原生 Windows 机器实测，本地 macOS 检查不能替代这些验收。

安装和启动入口都会校验 75 个内置源码文件；安装时该检查位于下载 Miniforge、安装依赖之前，缺少 `musetalk/models/*.py` 会立即报错。初始化失败会直接打印 worker 日志尾部；也可手动执行 `Get-Content .runtime\musetalk\worker.log -Tail 160`。批处理入口会优先使用 64 位 PowerShell，并保留失败退出码。

Windows 上验证真实视频时，使用已包含 Conda DLL/FFmpeg 环境配置的入口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\smoke_musetalk.ps1 -Video "D:\media\speaker.mp4" -Audio "D:\media\voice.wav" -Output "storage\verification\speaker_audio.mp4" -Timeline audio
```

`-Timeline audio` 对应 Linux 的 `--timeline audio`，允许音视频长度不同，默认连续验证两次；增加 `-Repeat 1` 可只执行一次。输出文件必须尚未存在。默认 strict 模式仍要求时长差不超过 150ms；该要求不是 Windows 安装错误。

### Ubuntu

前提：Ubuntu、能正常运行 `nvidia-smi` 的 NVIDIA 驱动，以及 PATH 中可用的 Conda。首次安装系统库可能要求 sudo 密码；无需另装 CUDA Toolkit，使用 PyTorch cu118 wheel。驱动需与该运行时兼容，安装脚本会实际检查 CUDA 和 MMCV 算子。

在本项目根目录执行：

```bash
bash install.sh
bash start.sh
```

安装脚本会创建项目内 `.conda/musetalk` Python 3.10 环境，安装 PyTorch 2.0.1/CUDA 11.8 和 MMCV 2.0.1 等依赖，下载并校验完整推理权重，最后执行模型初始化检查。已有开发 `.venv` 不会被替换。此依赖清单是集成基准，尚不是从成功服务器导出的完整锁定环境。

权重约数 GB，存放在 `models/`，不提交 Git；包含 v15 UNet、VAE、Whisper、DWPose、BiSeNet、ResNet18 和 S3FD。不下载 v1、训练用 SyncNet 或演示素材。下载支持断点续传及校验后跳过；中断后重新运行安装脚本即可。Hugging Face 文件按固定仓库提交下载，校验方式见 [第三方来源](third_party/README.md)。

如模型站点访问受限，可在安装前设置镜像或你服务器上实际可用的代理：

```bash
export HF_ENDPOINT=https://hf-mirror.com
# 可选：代理地址必须能从这台部署机器访问
# export HTTPS_PROXY=http://127.0.0.1:7890
bash install.sh
```

启动时先加载模型；加载失败则终止启动，具体信息见 `.runtime/musetalk/worker.log`。模型加载成功后才开始接收 HTTP 请求：

```bash
curl http://127.0.0.1:8081/
curl http://127.0.0.1:8081/health
```

`GET /` 保持原返回 `{"code":0,"msg":"success","data":{}}`；`/health` 返回 `ready` 和支持的 `timelines`；就绪时 HTTP 200，恢复中或不可用时 HTTP 503。接口文档位于 `/docs`，传输契约见 [API.md](API.md)。

跨机器调用：

```bash
export LIPSYNC_HOST=0.0.0.0
export LIPSYNC_API_KEY='replace-with-your-own-key'
bash start.sh
```

## 推理行为

模型运行在常驻 Python 子进程中，通过进程间通信接收任务，直接调用内置 MuseTalk 模块。连续任务复用已加载权重；没有启动 `scripts.inference` 命令行。视频解码、编码和音轨封装继续使用 FFmpeg。

同一 GPU 串行推理，只启动一个 HTTP worker。模型超时或任务失败会回收推理进程及其 FFmpeg 子进程，并重新加载模型；失败任务保持 failed，不自动重复执行。空闲进程意外退出也会自动恢复，恢复失败每 30 秒重试。模型恢复期间健康检查和新任务提交可能返回 503。队列中的任务按原顺序处理，恢复失败时相应任务可能失败。

正常关闭时先通知空闲推理进程退出，最多等待 10 秒，让 Python 清理信号量等资源；超过等待时间才强制回收进程树。异常和推理超时仍使用强制回收。

输入视频须为恒定帧率、有可检测人脸的视频。HTTP 提交任务可选择 `timeline`：默认 `strict` 要求共享起点、时长差不超过 150ms，保持原帧数、顺序和帧率；显式 `audio` 按完整音频长度生成画面，允许正放/倒放循环或提前结束输出。两种模式均保留小数帧率采样、完整解码、预期时长和上传音轨校验；变帧率会失败；未检测到人脸的帧保留原始画面并跳过口型推理，口型效果仍需人工检查。同一常驻模型可以交替处理两种模式。

推理保留输入显示分辨率，按 `LIPSYNC_FRAME_MEMORY_MB` 的像素预算分块处理（最多 32 帧，至少 1 帧），每块结束即删除临时图片；正向播放复用解码器，倒放循环按块寻帧。输出通过单个 FFmpeg 编码器持续写入，不保存整片生成图片。编码使用 H.264 CRF 18，保留分辨率不代表无损重编码；现有 BGR24 管线不保留 HDR/高位深。

音频按原有 30 秒分段提取 Whisper 特征，最多缓存两个分段到 CPU，仅当前推理批次进入 GPU；全局音频索引跨块保持连续。临时 PCM 为 16 kHz 单声道 float32（每小时约 220 MiB），磁盘仍需容纳上传文件、PCM 与输出。像素预算不是进程总内存上限：模型、解码器、编码器、单帧及工作缓冲另占内存；更高分辨率仍需更大的单帧资源，但帧缓存不再随视频时长增长。

整片逐帧时间戳校验改为流式读取，不再有固定 120 秒总时限；持续有输出就继续，卡住才触发媒体无进展超时。worker 默认取消推理总时限，保留无进展超时，可通过环境变量重新设置总时限。任务目录隔离并按 TTL 清理；重启清除旧任务状态和临时运行数据，需重新上传提交。

## 配置

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `LIPSYNC_HOST` | `0.0.0.0` | Linux/Windows 启动脚本监听地址，可通过环境变量覆盖 |
| `LIPSYNC_PORT` | `8081` | 启动脚本监听端口 |
| `LIPSYNC_GPU` | `0` | CUDA_VISIBLE_DEVICES 值，通常为一个 GPU 序号 |
| `LIPSYNC_BATCH_SIZE` | `1` | 推理批大小 |
| `LIPSYNC_FLOAT16` | `1` | 1 使用 FP16；0 使用 FP32 |
| `LIPSYNC_MODEL_DIR` | 项目下 models | 权重根目录，建议绝对路径 |
| `LIPSYNC_STARTUP_TIMEOUT` | `600` | 模型加载超时，秒 |
| `LIPSYNC_INFERENCE_TIMEOUT` | `0` | MuseTalk worker 总时限，秒；0 不限总时长，正数仍为硬时限；command 后端默认 3600 且要求正数 |
| `LIPSYNC_IDLE_TIMEOUT` | `600` | worker 无进展超时，秒；报错包含最近处理阶段 |
| `LIPSYNC_MEDIA_IDLE_TIMEOUT` | `120` | 媒体进程无输出/单帧读写/收尾等待超时，秒 |
| `LIPSYNC_FRAME_MEMORY_MB` | `256` | 帧块像素预算 MiB；按分辨率自动计算块大小，并预留三倍像素空间 |
| `LIPSYNC_API_KEY` | 空 | `/v1` 接口 Bearer 密钥 |
| `LIPSYNC_STORAGE` | 项目下 storage | 启动脚本的临时存储目录 |
| `LIPSYNC_TTL` | `86400` | 未提交或已结束会话保留时长，秒 |
| `LIPSYNC_CAPACITY` | `32` | 会话总容量 |
| `LIPSYNC_MAX_FILE` | `5368709120` | 单个输入文件上限，字节 |
| `LIPSYNC_CHUNK_SIZE` | `8388608` | 单个上传请求上限，字节 |

Python 服务默认选择 MuseTalk；`start.sh` / `start.ps1` 会明确选择 MuseTalk，并清除旧的 `LIPSYNC_COMMAND`。自定义部署仍可用 `LIPSYNC_ENGINE=command` 和 `LIPSYNC_COMMAND` JSON 数组接入其他模型，约定为 `{video}`、`{audio}`、`{output}` 三个绝对路径占位符。该模式用于兼容，不是正常 MuseTalk 部署所需步骤。

## 原服务器基准与 GPU 验收

先在已跑通的服务器激活原环境，执行本项目脚本。它只读取原 MuseTalk 目录，记录写入本项目：

```bash
conda activate musetalk
bash scripts/capture_baseline.sh "$HOME/apps/MuseTalk"
```

对比 `deployment-records/server-baseline/musetalk.commit.txt` 与 `third_party/MuseTalk.source.json` 的提交号，并核对依赖和模型哈希。版本不一致时需要先评估差异，不能直接认为已经复现原环境。安装成功后的新环境记录位于 `deployment-records/`。

部署后使用原有测试素材执行两次真实推理，检查模型复用：

```bash
.conda/musetalk/bin/python scripts/smoke_musetalk.py \
  --video /absolute/path/speaker.mp4 \
  --audio /absolute/path/voice.wav \
  --output "$PWD/storage/verification/speaker_synced.mp4"
```

默认生成两个输出及 `.verification.json`，输出文件名必须是新的。脚本验证时长、解码、音轨并记录耗时和进程号；需要人工播放输出，对照原结果检查口型、脸部融合和音画同步。该步骤通过后，再通过 HTTP 上传到下载跑一次完整链路。初始化成功不能替代这些验收。

如果原 MuseTalk 测试素材的音视频时长不同，验证脚本可增加 `--timeline audio`：

```bash
.conda/musetalk/bin/python scripts/smoke_musetalk.py \
  --video "$HOME/apps/MuseTalk/test_inputs/speaker.mp4" \
  --audio "$HOME/apps/MuseTalk/test_inputs/voice.wav" \
  --output "$PWD/storage/verification/speaker_audio.mp4" \
  --timeline audio
```

该模式按完整音频长度生成视频（取整到视频帧），音频较长时使用原版 MuseTalk 的“正放、倒放”循环帧顺序，音频较短时只生成对应时长的视频。输入文件不变，完整解码、输出时长和音轨校验仍保留。`.verification.json` 记录时间线模式和预期时长。
默认 `--timeline strict` 仍保留源视频时间线并要求时长差不超过 150ms；错误会在加载模型前提示双方时长。HTTP 服务和 `RemoteLipSync` 也支持显式 audio 模式；需要同步更新客户端与服务端，不能只取消客户端时长校验。

## VideoPrinterTurbo 调用

客户端接口保持不变，在 VideoPrinterTurbo 的 `server/` 中使用：

```python
import os
from pipeline.lip_sync.remote import RemoteLipSync

service = RemoteLipSync(
    base_url="http://192.168.1.100:8081",
    api_key=os.environ.get("LIPSYNC_API_KEY", ""),
    request_timeout=60,
    poll_interval=2,
    job_timeout=7200,
    readiness_timeout=600,
    timeline="strict",  # 不等长素材显式改为 "audio"
    chunk_size=8 * 1024 * 1024,
    retries=3,
    retry_delay=1,
)
result = service.lip_sync("speaker.mp4", "final_voice.wav", "speaker_synced.mp4")
```

支持上传/下载续传、提交幂等和 SHA-256 校验；检查点不保存密钥。失败任务不会自动重做；主动重试需移除客户端检查点或换新输出路径。本次同步更新了口型客户端和 HTTP 协议，未改 PipelineManager、数据库或前台。

## 本地测试与目录

`pyproject.toml` / `uv.lock` 管理轻量服务开发依赖；GPU 部署使用 `deployment/` 和 `install.sh`，仅执行 `uv sync` 不会安装模型运行环境。完整协议测试读取相邻 `../VideoPrinterTurbo/server` 的真实客户端代码，两个仓库需同级，测试不修改客户端源码。

```bash
mkdir -p .runtime/tmp
UV_CACHE_DIR="$PWD/.cache/uv" PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/.runtime/tmp" \
  uv run --with pytest --with httpx --with requests python -m pytest -q tests --basetemp=.runtime/pytest
```

测试中复制画面的引擎仅验证协议与进程管理，不验证模型效果；禁止监听端口的环境会跳过 TCP 用例。

- `third_party/MuseTalk/`：固定提交的上游源码和许可证。
- `lipsync_service/musetalk_engine.py`：进程生命周期、通信、超时和恢复。
- `lipsync_service/musetalk_runtime.py`：模型加载、逐帧推理及时间线适配。
- `lipsync_service/model_assets.py`：下载、续传与权重校验。
- `deployment/`：推理依赖与权重清单。
- `scripts/`：环境检查、成功环境记录和真实 GPU 验证。
- `doc/MuseTalk/INSTALL_cn.md`：历史独立安装步骤，保留供基准对照；新用户使用本文入口。


## 远程调用与超时预算

客户端新增 `timeline="strict"|"audio"`、`readiness_timeout=600`。`/health` 必须声明 audio 能力才提交 audio 任务，返回任务还需确认模式和预期时长。提交时省略请求体仍为 strict，同模式重提返回原任务，换模式重提同一上传返回 409。检查点记录模式，切换模式会新建会话；旧检查点只在 strict 下迁移。

`request_timeout=60` 限制单次 HTTP 请求；`readiness_timeout` 限制一次模型就绪/恢复等待；`job_timeout=7200` 从输入预检和哈希完成后开始，涵盖就绪等待、上传、排队、推理等待、下载和结果验证。503 恢复等待不再仅重试 6 秒，但始终受这两个时间预算限制。401/403/422 等明确错误不会反复等待。

根据当前 12.564 秒素材实测约 279–316 秒推理，以下仅为配置示例，不能替代目标机器测量：

| 场景 | 服务端 LIPSYNC_INFERENCE_TIMEOUT | 客户端 job_timeout | readiness_timeout |
| --- | --- | --- | --- |
| 一分钟以内、少量排队 | 3600 | 7200 | 600 |
| 三至五分钟素材 | 14400 | 21600 | 600 |
| 多任务排队 | 按单任务耗时设定 | 额外包含前面任务的排队耗时 | 600 |

修改服务端推理超时后需重启服务。客户端超时不取消远程任务，使用相同输入、地址、模式和输出路径再次调用可继续原任务；失败任务不自动重做。Windows 不支持硬链接的文件系统使用独占重命名发布，权限不足/磁盘满仍报错并保留已下载文件和检查点。

真实 HTTP/GPU 验收可在相邻 `VideoPrinterTurbo/server/` 的 Python 环境运行（本机须有 FFmpeg/ffprobe）：

```bash
python -m pipeline.lip_sync.check_remote \
  --base-url http://192.168.1.100:8081 \
  --video /absolute/path/speaker.mp4 --audio /absolute/path/voice.wav \
  --output /absolute/path/new_synced.mp4 --timeline audio
```

服务地址使用实际局域网 IP。API 密钥通过 `LIPSYNC_API_KEY` 环境变量提供；工具输出不含密钥的 `.remote-verification.json`，记录任务 ID、模式、耗时、音视频时长和哈希。strict 验收使用已对齐素材，仍需播放输出检查口型与画面。
