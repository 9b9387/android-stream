# android-stream

一个基于 scrcpy 协议的 Python SDK，用于实时获取 Android 屏幕帧；同时内置一个
基于 scrcpy **v4.0** 协议的浏览器直连桥接，支持 H.264 + Opus 直推 + 设备控制。

## 能力

- 实时帧流（单设备）
- `on_frame` 回调（`FramePacket`）
- 帧转 JPEG / base64
- 按需保存截图
- 状态与统计（`state`、`stats`）
- 内置 Web 推流服务（FastAPI + WebSocket）
- **scrcpy v4.0 直连桥接**：H.264 / Opus 原始包通过单条 WebSocket 转发，浏览器
  端通过 `WebCodecs` 原生硬解码；同一通道发送触摸 / 按键 / 文本注入等控制消息

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

## Web 推流服务

项目内置了一个最小 Web 服务，后端会把每一帧编码为 JPEG 并通过 WebSocket 推送给浏览器。
默认会以 `max_size=720` 请求设备视频流，避免网页显示过大。

启动服务：

```bash
uv run uvicorn android_stream.web:app --host 0.0.0.0 --port 8000
```

接口说明：

- `GET /health`：查看当前流状态与统计
- `WS /ws/stream`：二进制 JPEG 帧流（每条消息是一张 JPEG）

前端最小示例：

```html
<img id="screen" />
<script>
  const img = document.getElementById("screen");
  const ws = new WebSocket("ws://127.0.0.1:8000/ws/stream");
  ws.binaryType = "arraybuffer";
  ws.onmessage = (event) => {
    const blob = new Blob([event.data], { type: "image/jpeg" });
    const url = URL.createObjectURL(blob);
    img.src = url;
    img.onload = () => URL.revokeObjectURL(url);
  };
</script>
```

### 本地演示页面（开箱即用）

项目已提供示例页面：`web_demo/index.html`。

1) 启动推流后端：

```bash
uv run uvicorn android_stream.web:app --host 0.0.0.0 --port 8000
```

2) 在项目目录启动静态文件服务：

```bash
uv run python -m http.server 8080
```

3) 浏览器打开：

- `http://127.0.0.1:8080/web_demo/index.html`

页面默认连接 `ws://127.0.0.1:8000/ws/stream`，支持手动断开/重连并显示实时 FPS。

录屏由服务端完成，默认保存到 `.data/recordings`；也可以通过环境变量配置：

```bash
OMNI_FLOW_RECORDINGS_DIR=/path/to/recordings uv run uvicorn android_stream.web:app --host 0.0.0.0 --port 8000
```

演示页面提供开始、结束、状态查询和下载最近一次录屏文件的按钮。

## scrcpy v4.0 浏览器直连桥接

旧链路是 scrcpy 2.4 + 服务端 H.264→JPEG / WebRTC 重编码。新链路则与
[scrcpy 4.0](https://github.com/Genymobile/scrcpy/releases/tag/v4.0) 协议保持一致：
后端只解析帧封装，**H.264 / Opus 原始包**通过单条 WebSocket 直接送到浏览器，
由 `WebCodecs` 原生硬解码后渲染到 `<canvas>`；同一通道双向走 JSON 控制消息
（触摸 / 滚动 / 物理按键 / 文本注入 / 剪贴板）。

### 架构

```
            [Android device]
                  │  ADB
                  ▼
        ┌───────────────────┐
        │ scrcpy-server v4.0 │ (上行 video / audio，双向 control)
        └─────────▲─────────┘
                  │ TCP via adb forward
        ┌─────────┴─────────┐
        │ ScrcpyV4Backend    │ (Python，零解码，纯协议转发)
        │ ScrcpyV4Service    │ (asyncio fan-out, 多订阅者)
        └─────────▲─────────┘
                  │ WebSocket (二进制 16 字节头 + 有效载荷)
        ┌─────────┴─────────┐
        │  Web client        │ WebCodecs.VideoDecoder / AudioDecoder
        └────────────────────┘
```

### 启动

启用 v4 桥接（默认关闭，避免影响旧链路）后启动 uvicorn：

```bash
ANDROID_STREAM_SCRCPY_V4=1 \
  uv run uvicorn android_stream.web:app --host 0.0.0.0 --port 8000
```

启动时会：

1. 自动从 GitHub Releases 下载 `scrcpy-server-v4.0`（带 SHA256 校验，缓存到
   `~/.cache/android-stream/`）；
2. 通过 ADB 推送到 `/data/local/tmp/`，并以 `tunnel_forward=true` 模式启动；
3. 建立 video / audio / control 三条 socket，握手后开始转发。

### 新增端点

- `GET /scrcpy/info`：服务端配置 + 当前会话元数据（设备名、视频分辨率、编解码）
- `WS /ws/scrcpy`：浏览器直连入口
  - 服务端 → 客户端：1 个 JSON 初始化包 + 二进制媒体包流
  - 客户端 → 服务端：JSON 控制消息

二进制包前 16 字节为定长头：

```
byte  0      kind        1 = video, 2 = audio, 3 = video session
byte  1      flags       bit0=config, bit1=key_frame, bit2=client_resized
bytes 2-3    reserved
bytes 4-7    payload size (uint32 BE)
bytes 8-15   pts (uint64 BE)；session 包高/低 32 位编码 width/height
bytes 16+    H.264 NALU / Opus packet
```

控制 JSON 消息示例：

```jsonc
{ "type": "touch", "action": "down", "pointerId": "mouse",
  "x": 100, "y": 200, "screenWidth": 720, "screenHeight": 1280 }
{ "type": "key",  "action": "down", "keycode": 4 }    // KEYCODE_BACK
{ "type": "back", "action": "down" }                  // 等价于 BACK_OR_SCREEN_ON
{ "type": "text", "text": "你好" }
{ "type": "scroll", "x": 100, "y": 200,
  "screenWidth": 720, "screenHeight": 1280, "vScroll": -2 }
{ "type": "set_clipboard", "text": "hi", "paste": true, "sequence": 1 }
{ "type": "set_display_power", "on": false }
{ "type": "rotate" }
```

### 演示页面

```bash
ANDROID_STREAM_SCRCPY_V4=1 uv run uvicorn android_stream.web:app --host 0.0.0.0 --port 8000
# 另开终端
uv run python -m http.server 8080
```

浏览器打开 [`http://127.0.0.1:8080/web_demo/scrcpy.html`](http://127.0.0.1:8080/web_demo/scrcpy.html)
即可看到画面，鼠标 / 触摸 / 键盘均可控制设备。需要 Chrome/Edge 94+、
Safari 16.4+ 或 Firefox 130+ 以获得 WebCodecs 支持。

### 在 Python 里直接使用

```python
from android_stream import ScrcpyV4Service, ScrcpyV4Config, ControlMessage

service = ScrcpyV4Service(ScrcpyV4Config(max_size=720, max_fps=30))
meta = service.start()
print(meta.device_name, meta.width, meta.height, meta.video_codec)

# 在自己的 asyncio 循环里订阅原始 H.264/Opus 包
async def consume():
    sub = service.subscribe()
    async for packet in sub:
        ...  # 写入文件 / 转发 / 二次封装
    sub.close()

# 注入控制消息（比如 HOME 键）
service.send_control_message(ControlMessage.inject_keycode(action=0, keycode=3))
service.send_control_message(ControlMessage.inject_keycode(action=1, keycode=3))

service.stop()
```