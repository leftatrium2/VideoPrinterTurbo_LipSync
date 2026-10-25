# 本地验证与服务器验收边界

本地环境为 macOS，无法执行目标 NVIDIA CUDA 推理。

本地测试使用已安装的相邻项目 Python 解释器读取 pytest 等依赖；禁用字节码写入，临时目录和测试缓存都放在本项目内，不修改相邻源码或环境。

```bash
mkdir -p .runtime/tmp
PYTHONPATH="$PWD/.runtime/test-deps" PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/.runtime/tmp" \
  ../VideoPrinterTurbo/server/.venv/bin/python -m pytest -q tests --basetemp=.runtime/pytest-final
```

已有协议用例覆盖 ASGI 和真实 Uvicorn/TCP、续传、幂等、校验、串行队列、过期和锁。新增测试使用真实 spawn 进程及 FFmpeg 复制画面，验证模型进程复用、错误/崩溃/超时回收和下一任务恢复、空闲崩溃恢复、初始化失败释放锁。另验证小数帧率采样、变帧率拒绝、路径工作区、权重续传/损坏拒绝及源码哈希。

首次集成结果：**34 passed，0 skipped，退出码 0**，耗时 32.69 秒。包含真实本机 TCP 用例。
测试依赖中的 OpenCV 4.9.0.80 和 NumPy 1.23.5 只安装到本项目 `.runtime/test-deps`，未修改相邻 Python 环境。
真实张量测试验证官方音频上下文对齐；模拟神经网络配合真实 FFmpeg 验证 30000/1001 FPS 的 30 帧输入输出一致和缺失人脸拒绝。模拟模型不代表口型效果。

其余检查：Bash 语法检查、项目 Python 3.10 语法检查、JSON 解析、75 个内置上游文件哈希及 `git diff --check` 均通过。macOS 上安装脚本按预期拒绝非目标平台。
两条已有 WebSocket 弃用警告不影响测试结果。

没有执行真实 CUDA 模型加载、推理、权重全量下载或干净 Ubuntu 安装；服务器待办见 [INTEGRATION_TODO.md](INTEGRATION_TODO.md)。

## 原生 Windows 入口增量验证

增加 Windows 安装和启动脚本后，全量回归为 **42 passed，0 skipped，退出码 0**，耗时 33.09 秒。
新增用例检查目录联接的安全参数传递、Python 3.10 的联接识别、同盘硬链接/跨盘复制、Job Object 句柄所有权与失败清理、引擎回收 Job Object，以及真实 worker 初始化门控与失败退出。
Win32 API 调用在 macOS 上使用模拟接口，不能据此声称已完成 Windows 实机测试。

在本项目 `.tools/powershell` 中使用经官方发布哈希校验的便携 PowerShell 7.6.8 解析 `install.ps1`、`start.ps1`、`scripts/windows_common.ps1`，三者均无语法错误；平台守卫及原生命令失败退出码传播检查通过。脚本使用 PowerShell 5.1 兼容语法，Windows 自带 PowerShell 5.1 的实际运行仍待验收。

已核对官方发布资源：

- [MMCV cu118 / torch2.0 wheel 索引](https://download.openmmlab.com/mmcv/dist/cu118/torch2.0/index.html)：包含 `mmcv-2.0.1-cp310-cp310-win_amd64.whl`。
- [xtcocotools 1.14.3 发布文件](https://pypi.org/project/xtcocotools/1.14.3/#files)：包含 CPython 3.10 Windows x64 wheel。
- [Miniforge 官方发布清单](https://conda-forge.org/miniforge/)：安装器固定为 26.7.2-1 Windows x86_64，并在执行前验证 SHA-256。
- [Conda Windows 静默安装参数](https://docs.conda.io/projects/conda/en/latest/user-guide/install/windows.html#installing-in-silent-mode)。
- [Windows Job Object 生命周期](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。

仍需在原生 Windows 10/11 x64 + NVIDIA 机器执行完整安装、DLL/CUDA/MMCV 算子检查、目录联接、进程树回收及真实口型效果验收。

## 服务器源码漏打包修复

服务器已通过 RTX 3080 的 CUDA/MMCV 算子检查及所有推理权重校验，但初始化报告 `ModuleNotFoundError: No module named 'musetalk.models'`。
根因是项目 `.gitignore` 中未锚定的 `models/` 同时忽略了 `third_party/MuseTalk/musetalk/models/` 下的 Python 源码。
本地源码存在，因此此前只检查文件哈希的测试未发现 Git 同步漏文件。

回归测试先复现失败：Git 明确忽略了 `syncnet.py`、`unet.py` 和 `vae.py`。将规则修正为 `/models/` 后，三个源码文件可以纳入版本控制，完整上游清单不再有被忽略的文件，根目录下载权重仍被忽略。
新增源码预检会在环境检查及模型加载时验证 75 个源码文件，缺失或改变时报告具体名称，无需先加载 CUDA。

部署本次修复时必须一并同步以下三个源码文件，仅更新 `.gitignore` 无法补回已遗漏的文件：

```text
third_party/MuseTalk/musetalk/models/syncnet.py
third_party/MuseTalk/musetalk/models/unet.py
third_party/MuseTalk/musetalk/models/vae.py
```

同步后在服务器项目根目录执行：

```bash
.conda/musetalk/bin/python -m lipsync_service.source_assets
.conda/musetalk/bin/python scripts/check_environment.py --load-model
```

本次全量回归：**45 passed，0 skipped，退出码 0**，耗时 33.93 秒，包含真实 TCP 和新增源码打包检查。
无需重新安装现有依赖或下载已通过校验的权重。此修复的本地回归验证不能代替服务器实际模型初始化复验。

## 原版音频时长模式

服务器的原 MuseTalk 素材通过初始化后，在 `validate_inputs` 被额外的 150ms 对齐约束拒绝。
使用 1 秒视频和 2 秒音频的回归用例复现同一 ValueError，确认不是模型推理失败。
新增 `MuseTalkEngine(timeline='audio')` 和验证脚本 `--timeline audio`，按音频长度生成视频，并与音频时长比较输出；默认 strict 模式不变。
逐帧测试使用真实 FFmpeg 和模拟神经网络，验证长音频的正放/倒放循环顺序、短音频的输出帧数、30000/1001 帧率及完整音轨封装。模型效果仍待服务器真实推理检查。
本次全量回归：**48 passed，0 skipped，退出码 0**，耗时 36.87 秒，包含默认严格模式的原协议回归。

## 服务器真实推理与正常关闭资源清理

服务器 RTX 3080 已完成 audio 模式的两次真实推理：输入音频 12.564 秒，两个输出的视频轨均为 12.56 秒、音频轨均为 12.564 秒；两次 worker PID 相同，确认常驻模型复用。模型加载约 53.90 秒，推理分别约 315.96 秒、278.57 秒。视频口型和脸部融合仍需人工验收。

推理结束后的 `resource_tracker: There appear to be 1 leaked semaphore objects` 来自退出清理路径。此前 `close()` 无条件强杀空闲 worker，跳过其 Python 信号量清理函数。
使用真实 spawn 子进程创建一个 Semaphore，并捕获整个解释器退出的标准错误，复现了完全相同的警告。改为正常关闭时发送已有协议的 None 通知、限时 join 后，该回归用例通过且没有该警告。进程树回收及 Windows Job Object 关闭保留在等待之后；故障路径仍强制终止。

新增不响应关闭通知的 worker 测试，验证等待超时后依旧强制回收，避免优雅关闭导致服务挂住。这些测试验证进程管理，不替代服务器下一次实际推理后的退出复验。
本次全量回归：**50 passed，0 skipped，退出码 0**，耗时 38.29 秒。真实信号量用例从同样的警告失败变为无警告通过，未使用警告过滤或手动注销来隐藏问题。
