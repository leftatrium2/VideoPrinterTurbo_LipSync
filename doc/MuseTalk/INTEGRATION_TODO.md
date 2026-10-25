# MuseTalk 1.5 集成实施与验收清单

修改范围：仅 `VideoPrinterTurbo_LipSync/`。原服务器 `~/apps/MuseTalk`、相邻客户端和其他项目不修改。

## 已实现

- [x] 内置官方源码到 `third_party/MuseTalk/`，保留许可证、固定提交号及逐文件 SHA-256。
- [x] 排除上游 Git 元数据、演示素材、生成结果和大模型权重。
- [x] 默认 Python 版本调整为 3.10，并更新轻量服务锁文件；部署环境位于 `.conda/musetalk`。
- [x] 单独整理推理依赖，保留 PyTorch/MMCV 特殊 wheel 安装步骤；未合并训练和 Gradio 依赖。
- [x] `MuseTalkEngine` + 常驻 spawn Python 子进程，模型加载一次、连续任务复用。
- [x] 默认 v15、GPU 0、batch_size=1、FP16。
- [x] 子进程独立资源工作区和项目内缓存，HTTP 进程工作目录不变，上游源码保持原样。
- [x] 串行队列、任务临时目录隔离、失败处理、超时回收进程组及自动重新加载。
- [x] 空闲推理进程意外退出后由监控线程恢复，失败每 30 秒重试。
- [x] 保留输出解码、时长、音轨封装校验，失败任务不自动重做。
- [x] 按源帧数、帧序、原始有理数帧率生成结果，取消视频循环，并保留小数帧率音频采样。
- [x] 明确拒绝变帧率和缺失人脸的输入，不跳帧生成表面成功结果。
- [x] `install.sh` / `start.sh`、完整推理权重清单、断点下载和完整性校验。
- [x] GPU/MMCV 算子检查与实际模型初始化检查工具。
- [x] `GET /health`、启动失败释放存储锁，默认不再要求 `LIPSYNC_COMMAND`。
- [x] 保留自定义命令引擎兼容入口。
- [x] 原成功环境采集脚本和连续两次真实推理验收脚本。
- [x] 更新中英文 README、HTTP 契约和第三方来源记录。
- [x] 新增进程复用、超时/崩溃/异常恢复、空闲崩溃、初始化失败、资源路径、帧率和下载校验测试。

## 必须在服务器完成

- [ ] 在原成功 Conda 环境运行 `bash scripts/capture_baseline.sh "$HOME/apps/MuseTalk"`。
- [ ] 核对原成功源码提交、依赖版本、CUDA/驱动和模型哈希；必要时调整集成基准。
- [ ] 保存原测试视频、配音和已验收输出，用于对照。
- [ ] 在干净 Ubuntu x86_64 + NVIDIA 环境执行 `bash install.sh`，验证完整依赖求解、所有下载和模型初始化。
- [ ] 使用原素材运行 `scripts/smoke_musetalk.py`，确认连续两次推理成功并复用进程。
- [ ] 人工检查口型同步、脸部融合、画面、音轨和时长，与原命令行成功结果对照。
- [ ] 使用真实模型完成一次 HTTP 上传、排队、推理、结果下载和续传验证。
- [ ] 在 GPU 环境检查实际显存占用、长视频内存/磁盘需求、CUDA 失败恢复和完整关闭流程。

内置提交：`0a89dec45a0192b824e3cf4daf96c239440c5ed8`，来自官方上游，**尚未证明与原服务器成功提交一致**。
当前依赖文件是安装基准，不是原成功服务器导出的完整传递依赖锁。初始化检查不代表视频效果通过验收。

## 本地验证记录

首次集成 34 项测试全部通过；增加原生 Windows 入口后，全量回归 42 项通过、无跳过。包含真实 TCP、进程恢复、实际媒体帧数和音频上下文检查。详见 [VERIFICATION.md](VERIFICATION.md)。本地假引擎只验证协议、媒体和进程管理，不验证神经网络口型效果。

## 原生 Windows 部署入口

- [x] `install.bat` / `install.ps1`，自动准备项目内 Miniforge、Python 3.10、FFmpeg 和 VC++ 运行库。
- [x] 核对 MMCV 2.0.1、xtcocotools 1.14.3 的 CPython 3.10 Windows x64 预编译包，禁止回退编译这些包。
- [x] Windows 启动脚本、Conda DLL 搜索路径、失败退出码、安装记录与代理处理。
- [x] 目录联接替代目录符号链接，S3FD 硬链接/复制，避免要求开发者模式。
- [x] 父进程持有 kill-on-close Job Object，初始化门控确保子进程先纳管再加载模型。
- [x] 保留内置上游文件原始字节，避免 Windows Git 换行转换破坏源码哈希。
- [x] PowerShell 解析、失败传播及跨平台资源管理回归。
- [x] 将 Windows 源码 SHA-256 预检前置到所有下载/安装步骤之前，并显示初始化失败日志尾部。
- [x] 检查 64 位 PowerShell/Sysnative 入口、WOW64 架构识别、退出码与 CRLF。
- [x] 增加配置 Conda DLL/FFmpeg 路径的 Windows 视频验证入口，支持 `-Timeline audio`。
- [x] 验证正常退出先等待 worker，再关闭 Job Object；全量回归增至 53 项通过。
- [ ] 在原生 Windows 10/11 x64 + NVIDIA 环境完整安装，并实际验证 CUDA/MMCV、模型加载和输出质量。
- [ ] 在 Windows 实测目录联接、Job Object、超时/崩溃恢复和正常退出后的 GPU/FFmpeg 回收。
