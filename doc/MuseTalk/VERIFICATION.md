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
