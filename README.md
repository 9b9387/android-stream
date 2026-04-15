# android-stream

一个类似 scrcpy 的 Python SDK，用于实时接收 Android 屏幕帧并触发 `onFrame` 回调。

## 功能

- 单设备实时视频帧监听（基于 scrcpy server 协议）
- `on_frame` 回调可获取 `FramePacket`（包含 `numpy.ndarray(BGR)`）
- 支持将当前帧转为 JPEG bytes / base64
- 支持按需将帧落盘

## 前置条件

- macOS / Linux / Windows
- 安装 `adb` 并可执行 `adb devices`
- Android 设备已开启 USB 调试并可被 `adb` 识别
- 本机已安装 Python 3.10+

## 使用 uv 管理项目

```bash
uv sync
```

可选：运行测试

```bash
uv run pytest -q
```

## 安装（pip 方式，可选）

```bash
pip install -e .
pip install pytest
```

说明：首次运行会自动下载 scrcpy server 文件到 `~/.cache/android-stream/` 并进行 sha256 校验。

## 快速开始

### 作为 Python SDK 使用（推荐）

```python
from android_stream import AndroidStreamSDK, StreamConfig

sdk = AndroidStreamSDK(StreamConfig(max_fps=30, bitrate=8_000_000))

def on_frame(packet):
    print(packet.width, packet.height)
    packet.save_jpeg("frames/latest.jpg")

sdk.on_frame(on_frame)
sdk.start()
# ... your app loop ...
sdk.stop()
```

### SDK API 速览

- `StreamConfig(device_serial=None, max_fps=30, bitrate=8_000_000)`：配置设备和视频参数
- `AndroidStreamSDK.start()/stop()`：启动与停止采集
- `AndroidStreamSDK.on_frame(callback)`：注册帧回调，返回 `unsubscribe()` 函数
- `AndroidStreamSDK.on_error(callback)`：注册错误回调
- `AndroidStreamSDK.on_state_change(callback)`：监听状态变化（`starting/running/stopping/stopped/error`）
- `AndroidStreamSDK.latest_frame`：获取最近一帧（`FramePacket | None`）
- `AndroidStreamSDK.wait_for_first_frame(timeout_s=5.0)`：等待首帧，常用于启动校验
- `AndroidStreamSDK.save_latest_frame(path, quality=90)`：保存最近一帧到磁盘
- `AndroidStreamSDK.stats`：获取统计信息（接收帧数、丢帧数、错误数）
- `create_client(config)`：创建 SDK 客户端的便捷工厂方法

### 异常类型（便于外部集成做精细处理）

- `AndroidStreamError`：所有 SDK 异常基类
- `StreamStartError`：启动阶段异常基类
- `DeviceNotFoundError`：未发现可用设备
- `ServerDownloadError`：scrcpy server 下载失败（含超时/重试耗尽）
- `ChecksumMismatchError`：下载文件哈希校验失败
- `HandshakeError`：协议握手失败
- `StreamDisconnectedError`：流中断
- `DecodeError`：解码异常

或运行示例：

```bash
python -m android_stream.examples.basic_monitor
```

### 命令行方式

```bash
./scripts/start_monitor.sh
```

或直接：

```bash
uv run android-stream-monitor --save-every 30 --print-base64-length
```

运行后会输出帧信息，并按参数把截图写入 `./frames/` 目录。

常用参数：

- `--device-serial <serial>` 指定设备序列号
- `--fps <int>` 目标帧率
- `--bitrate <int>` 视频码率
- `--save-dir <path>` 落盘目录
- `--save-every <N>` 每 N 帧保存一次
- `--print-base64-length` 打印当前帧 base64 长度（便于对接 LLM）

## 截图输出位置

- 默认目录：项目根目录下 `./frames/`
- 示例文件：`frames/frame_000005.jpg`
- 如果没有生成截图，通常是因为尚未收到帧（可先用 `--save-every 1` 验证）

## 常见问题

### 1) 启动后没有截图文件

先检查：

```bash
adb devices
```

然后用更高频保存观察：

```bash
uv run android-stream-monitor --save-every 1 --print-base64-length
```

如果持续没有 `frame=` 日志，优先确认手机 USB 调试授权状态，以及 `adb devices` 是否为 `device`。

### 2) macOS 出现 `objc ... AVFFrameReceiver` 警告

旧版本可能由于 `opencv-python` 与 `av` 同时携带 FFmpeg 动态库出现该告警。当前版本已移除 `opencv-python` 依赖，若你仍看到告警，请先执行：

```bash
uv sync
```
