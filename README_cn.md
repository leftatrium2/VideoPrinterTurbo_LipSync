<div align="center">
<h1 align="center">VideoPrinterTurbo_LipSync</h1>
<br>
<h3><a href="README.md">English</a> | 简体中文</h3>
<br>
</div>
# VideoPrinterTurbo_LipSync

供 VideoPrinterTurbo 调用的本地 LipSync 后端，使用 Python 3.11–3.12、uv、FastAPI 和 Uvicorn，仅提供 API，无前台界面。

## 启动

依赖已配置完成。当前本机已验证的启动命令（在项目根目录执行）：

```sh
/Users/sunxiao5/.local/bin/uv run /Users/sunxiao5/opensource/agent/VideoPrinterTurbo_LipSync/.venv/bin/python -m uvicorn app:app --port 8081
```

默认仅监听本机，端口 8081 避开 VideoPrinterTurbo 的 8080。
跨机器调用时可将 `--host` 改为 `0.0.0.0`。

## 接口

```sh
curl http://127.0.0.1:8081/
```

`GET /` 返回：

```json
{"code": 0, "msg": "success", "data": {}}
```

FastAPI 自动生成的接口文档：<http://127.0.0.1:8081/docs>。

## 目录

- `app.py`：应用配置、生命周期、应用工厂与路由注册。
- `routers/index.py`：根接口；`routers/lipsync.py`：上传、任务、结果下载 API 与鉴权。
- `lipsync_service/`：推理命令执行、任务队列与存储管理。
- `utils/`：响应封装与公共常量，沿用 VideoPrinterTurbo 的成功响应格式。
- `pyproject.toml`：uv 依赖配置；首次成功执行 `uv sync` 后生成 `uv.lock`。

现已提供断点上传、串行任务队列、结果续传和命令式模型接入。HTTP 契约见 [API.md](API.md)。
模型及权重需在 CUDA 机器单独安装；本仓库不内置模型，也不会用普通音画封装冒充口型同步。


## CUDA 服务部署

在项目目录执行 `uv sync`，安装 FFmpeg/ffprobe 并确保在 PATH 中。
使用模型自己的 Python 环境及推理脚本，通过 JSON 参数数组配置命令：

```sh
export LIPSYNC_COMMAND='["/opt/model/.venv/bin/python", "/opt/model/run_lipsync.py", "--video", "{video}", "--audio", "{audio}", "--output", "{output}"]'
export LIPSYNC_API_KEY='replace-with-your-own-key'
uv run python -m uvicorn app:app --host 0.0.0.0 --port 8081 --workers 1
```

上述脚本路径和参数是**接入约定示例**，需替换为实际模型脚本，不是仓库内置脚本。
命令必须包含 `{video}`、`{audio}`、`{output}`，服务替换为绝对路径；不启动 shell。
脚本必须阻塞直到推理结束，成功返回 0，并在 `{output}` 写出非空 MP4。
脚本应使用绝对配置/权重路径；工作目录是本服务启动目录。
输入已对齐，禁止通过循环、变速、截断改变时间线。后台验证视频时长和完整解码，
然后保留模型画面，用上传配音重新封装音轨并验证结果。模型输出本身的口型质量仍需人工验收。

未配置命令时根接口可用，但提交任务返回 HTTP 503。
模型错误、缺少 FFmpeg、无输出、时长变化或解码失败均使任务进入 `failed`，不提供结果下载。
任务 ID 与异常类型写入服务日志；模型标准输出/错误保存在该任务临时目录的 `inference.log`，
随任务过期删除。HTTP 错误不会暴露模型命令及日志。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `LIPSYNC_COMMAND` | `[]` | 推理命令 JSON 数组 |
| `LIPSYNC_API_KEY` | 空 | 非空时 `/v1` 接口必须带 Bearer 密钥 |
| `LIPSYNC_STORAGE` | `storage` | 临时文件根目录，相对启动目录 |
| `LIPSYNC_INFERENCE_TIMEOUT` | 3600 | 单次模型命令超时，秒 |
| `LIPSYNC_TTL` | 86400 | 未提交上传、已结束任务的空闲保留时长，秒 |
| `LIPSYNC_CAPACITY` | 32 | 包括未提交和已结束会话的最大总数 |
| `LIPSYNC_MAX_FILE` | 5368709120 | 每个输入文件上限，字节 |
| `LIPSYNC_CHUNK_SIZE` | 8388608 | 单个上传请求上限，字节 |

只启动一个 worker。同一存储目录有进程锁，第二个实例启动失败；同一 GPU 也只部署一个实例。
元数据及队列只在内存中，重启后旧 ID 返回 404；启动时清理该存储目录的遗留运行文件。
正常退出等待当前任务结束（或命令超时），丢弃其余队列并清理临时文件。
运行中的任务和下载不会被 TTL 清理，查询进度和下载会刷新保留时间。
上传和下载支持网络中断后继续；服务端重启后的恢复是重新上传和提交，不恢复旧推理。

## VideoPrinterTurbo 调用

在 VideoPrinterTurbo 的 `server/` 目录：

```python
import os
from pipeline.lip_sync.remote import RemoteLipSync

service = RemoteLipSync(
    base_url="http://192.168.1.100:8081",
    api_key=os.environ.get("LIPSYNC_API_KEY", ""),
    request_timeout=60,
    poll_interval=2,
    job_timeout=7200,
    chunk_size=8 * 1024 * 1024,
    retries=3,
    retry_delay=1,
)
result = service.lip_sync("speaker.mp4", "final_voice.wav", "speaker_synced.mp4")
```

配置通过构造函数显式传入，与现有云端适配器一致，不改数据库或前端配置。
HTTP(S) 可用于自建服务地址；客户端直接连接，不读取环境代理。
若调小服务端分块上限，也需相应调小客户端 `chunk_size`。
结果路径必须是新的 `.mp4`，输入须共享起点且时长差不超过 150ms。
每个并发调用使用独立客户端实例，且输出路径不同。

客户端在结果旁保存 `.lipsync.json` 检查点与 `.lipsync.part` 下载文件，不保存密钥。
中断后以相同输入、服务地址及输出路径再次调用即可继续，换输入时自动重建会话。
输入改变或会话过期时，旧服务端文件等待 TTL 清理。失败任务保留原 ID，不自动重复推理；
需主动重试失败任务时，移除本地检查点或改用新输出路径。
`last_job_id` 提供最近任务 ID。本地超时不会取消远程任务。
成功后校验 SHA-256、音视频流、时长和完整解码，原子发布结果并删除检查点、下载临时文件。
`.lipsync.lock` 是保留的轻量锁文件，不代表任务仍在执行；异常退出后 OS 自动释放锁。
尚未接入 `PipelineManager`，不新增任务字段或前端入口。

## 验证

本仓库测试调用相邻 `../VideoPrinterTurbo/server` 的真实客户端代码，需两个仓库同级。
可使用已安装 pytest/httpx/requests 的环境，或：

```sh
uv run --with pytest --with httpx --with requests python -m pytest -q tests
```

测试包含 ASGI 进程内传输和真实 Uvicorn/TCP 两种路径；禁止监听端口的沙箱会跳过 TCP 用例。
测试引擎使用 FFmpeg 复制画面，**仅验证协议、队列、文件处理和结果返回，不验证口型效果**。
覆盖上传偏移与校验、响应丢失后的幂等恢复、客户端重启、服务端会话失效、Range/If-Range、
下载续传与损坏校验、串行排队、模型失败/超时、客户端超时、TTL 清理及进程锁。
