# android-stream

一个基于 scrcpy 协议的 Python SDK，用于实时获取 Android 屏幕帧。

## 能力

- 实时帧流（单设备）
- `on_frame` 回调（`FramePacket`）
- 帧转 JPEG / base64
- 按需保存截图
- 状态与统计（`state`、`stats`）

## 前置条件

- Python 3.10+
- 已安装 `adb`
- Android 设备开启 USB 调试并可被 `adb devices` 识别

## 安装

推荐 `uv`：

```bash
uv sync
```

可选运行测试：

```bash
uv run pytest -q
```

首次启动会自动下载 scrcpy server 到 `~/.cache/android-stream/`，并进行 SHA256 校验。

## 快速开始（SDK）

```python
from android_stream import AndroidStreamSDK, StreamConfig

sdk = AndroidStreamSDK(StreamConfig(max_size=1080, max_fps=30, bitrate=8_000_000))

def on_frame(packet):
    print(packet.width, packet.height)
    packet.save_jpeg("frames/latest.jpg")

sdk.on_frame(on_frame)
sdk.start()
# ... your app loop ...
sdk.stop()
```

## SDK API（简版）

- `StreamConfig(device_serial=None, max_size=0, max_fps=30, bitrate=8_000_000)`
- `max_size`：最长边尺寸限制（例如 `max_size=1080`；`0` 表示不限制）
- `AndroidStreamSDK.start()/stop()`
- `AndroidStreamSDK.on_frame(callback)`
- `AndroidStreamSDK.on_error(callback)`
- `AndroidStreamSDK.on_state_change(callback)`
- `AndroidStreamSDK.wait_for_first_frame(timeout_s=5.0)`
- `AndroidStreamSDK.latest_frame`
- `AndroidStreamSDK.save_latest_frame(path, quality=90)`
- `AndroidStreamSDK.stats`
- `create_client(config)`