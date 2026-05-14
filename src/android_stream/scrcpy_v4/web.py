"""FastAPI router exposing the scrcpy v4 bridge to a browser.

Single WebSocket endpoint ``/ws/scrcpy`` carries:

- *server -> client* binary frames containing ``MediaPacket`` payloads
  (H.264/H.265/AV1 NALUs and Opus/AAC/FLAC packets) prefixed by a small
  fixed-size header so the browser can route them to the right decoder.
- *server -> client* text frames carrying JSON envelopes for stream lifecycle
  events (``init``, ``state``, ``error``).
- *client -> server* text frames carrying JSON control messages
  (``touch``, ``key``, ``back``, ``home``, ``set_clipboard``, ...).

The binary header is 16 bytes, big-endian, see :func:`_encode_packet` below.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect

from android_stream.exceptions import AndroidStreamError
from android_stream.models import StreamState

from .protocol import (
    ACTION_DOWN,
    ACTION_MOVE,
    ACTION_UP,
    BUTTON_PRIMARY,
    KEY_ACTION_DOWN,
    KEY_ACTION_UP,
    POINTER_ID_GENERIC_FINGER,
    POINTER_ID_MOUSE,
    CONTROL_MSG_TYPE,
    ControlMessage,
)
from .service import MediaKind, MediaPacket, ScrcpyV4Config, ScrcpyV4Service

log = logging.getLogger(__name__)


# Binary frame layout for server -> client media:
#
#     byte  0      kind       1 = video, 2 = audio, 3 = video session
#     byte  1      flags      bit 0 = config packet (codec extradata)
#                             bit 1 = key frame
#                             bit 2 = client_resized (session only)
#     bytes 2-3    reserved   currently 0
#     bytes 4-7    payload size (uint32 BE)
#     bytes 8-15   pts in microseconds (uint64 BE)
#                  - for SESSION frames, the upper 32 bits encode width and
#                    the lower 32 bits encode height.
#     bytes 16+    payload
HEADER_SIZE = 16
KIND_VIDEO = 1
KIND_AUDIO = 2
KIND_SESSION = 3


def _encode_packet(packet: MediaPacket) -> bytes:
    flags = 0
    if packet.config:
        flags |= 0x01
    if packet.key_frame:
        flags |= 0x02

    if packet.kind == MediaKind.VIDEO:
        kind = KIND_VIDEO
        pts = packet.pts_us
        size = len(packet.payload)
        payload = packet.payload
    elif packet.kind == MediaKind.AUDIO:
        kind = KIND_AUDIO
        pts = packet.pts_us
        size = len(packet.payload)
        payload = packet.payload
    elif packet.kind == MediaKind.SESSION:
        kind = KIND_SESSION
        if packet.client_resized:
            flags |= 0x04
        # Session "pts" doubles as packed width/height for cheap parsing.
        pts = ((packet.width & 0xFFFFFFFF) << 32) | (packet.height & 0xFFFFFFFF)
        size = 0
        payload = b""
    else:  # pragma: no cover - exhaustive
        raise ValueError(f"unsupported media kind: {packet.kind}")

    header = struct.pack(">BBHIQ", kind, flags, 0, size, pts)
    return header + payload


# ---------------------------------------------------------------------------
# Control message JSON parsing
# ---------------------------------------------------------------------------


def _parse_control_json(payload: dict[str, Any]) -> ControlMessage:
    msg_type = (payload.get("type") or "").lower()

    if msg_type == "touch":
        action_raw = payload.get("action", "down")
        action = _touch_action(action_raw)
        pointer_id = _parse_pointer_id(payload.get("pointerId"))
        buttons = int(payload.get("buttons", BUTTON_PRIMARY if action != ACTION_UP else 0))
        action_button = int(
            payload.get(
                "actionButton",
                BUTTON_PRIMARY if action in (ACTION_DOWN, ACTION_UP) else 0,
            )
        )
        pressure = float(payload.get("pressure", 1.0 if action != ACTION_UP else 0.0))
        return ControlMessage.inject_touch_event(
            action=action,
            pointer_id=pointer_id,
            x=int(payload["x"]),
            y=int(payload["y"]),
            screen_width=int(payload["screenWidth"]),
            screen_height=int(payload["screenHeight"]),
            pressure=pressure,
            action_button=action_button,
            buttons=buttons,
        )

    if msg_type == "scroll":
        return ControlMessage.inject_scroll_event(
            x=int(payload["x"]),
            y=int(payload["y"]),
            screen_width=int(payload["screenWidth"]),
            screen_height=int(payload["screenHeight"]),
            h_scroll=float(payload.get("hScroll", 0.0)),
            v_scroll=float(payload.get("vScroll", 0.0)),
            buttons=int(payload.get("buttons", 0)),
        )

    if msg_type == "key":
        action = _key_action(payload.get("action", "down"))
        return ControlMessage.inject_keycode(
            action=action,
            keycode=int(payload["keycode"]),
            repeat=int(payload.get("repeat", 0)),
            meta_state=int(payload.get("metaState", 0)),
        )

    if msg_type == "text":
        return ControlMessage.inject_text(str(payload.get("text", "")))

    if msg_type == "back":
        return ControlMessage.back_or_screen_on(_key_action(payload.get("action", "down")))

    if msg_type == "home":
        # Equivalent to hardware HOME via keyevent; KEYCODE_HOME = 3.
        return ControlMessage.inject_keycode(
            action=_key_action(payload.get("action", "down")),
            keycode=3,
        )

    if msg_type == "app_switch":
        # KEYCODE_APP_SWITCH = 187.
        return ControlMessage.inject_keycode(
            action=_key_action(payload.get("action", "down")),
            keycode=187,
        )

    if msg_type == "power":
        # KEYCODE_POWER = 26.
        return ControlMessage.inject_keycode(
            action=_key_action(payload.get("action", "down")),
            keycode=26,
        )

    if msg_type == "expand_notifications":
        return ControlMessage.empty(CONTROL_MSG_TYPE.EXPAND_NOTIFICATION_PANEL)

    if msg_type == "expand_settings":
        return ControlMessage.empty(CONTROL_MSG_TYPE.EXPAND_SETTINGS_PANEL)

    if msg_type == "collapse_panels":
        return ControlMessage.empty(CONTROL_MSG_TYPE.COLLAPSE_PANELS)

    if msg_type == "rotate":
        return ControlMessage.empty(CONTROL_MSG_TYPE.ROTATE_DEVICE)

    if msg_type == "reset_video":
        return ControlMessage.empty(CONTROL_MSG_TYPE.RESET_VIDEO)

    if msg_type == "set_clipboard":
        return ControlMessage.set_clipboard(
            sequence=int(payload.get("sequence", 0)),
            text=str(payload.get("text", "")),
            paste=bool(payload.get("paste", False)),
        )

    if msg_type == "set_display_power":
        return ControlMessage.set_display_power(bool(payload.get("on", True)))

    raise ValueError(f"unsupported control message type: {msg_type!r}")


def _touch_action(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value).lower()
    if text in {"down", "pointer_down"}:
        return ACTION_DOWN
    if text in {"up", "pointer_up"}:
        return ACTION_UP
    if text == "move":
        return ACTION_MOVE
    raise ValueError(f"unknown touch action: {value!r}")


def _key_action(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value).lower()
    if text == "down":
        return KEY_ACTION_DOWN
    if text == "up":
        return KEY_ACTION_UP
    raise ValueError(f"unknown key action: {value!r}")


def _parse_pointer_id(value: Any) -> int:
    """Translate a JSON-friendly pointer id into the 64-bit wire value.

    Accepts the well-known string aliases ``"mouse"`` and ``"finger"`` as well
    as numeric IDs (which are passed through). Falls back to the generic
    finger constant when no value is provided. Numeric strings are also
    accepted to make manual debugging easier.
    """
    if value is None:
        return POINTER_ID_GENERIC_FINGER
    if isinstance(value, str):
        text = value.lower()
        if text == "mouse":
            return POINTER_ID_MOUSE
        if text == "finger":
            return POINTER_ID_GENERIC_FINGER
        try:
            return int(text, 0)
        except ValueError as exc:
            raise ValueError(f"unknown pointer id: {value!r}") from exc
    return int(value)


# ---------------------------------------------------------------------------
# Lifecycle helper
# ---------------------------------------------------------------------------


def create_scrcpy_v4_router(service: ScrcpyV4Service) -> APIRouter:
    router = APIRouter()

    @router.get("/scrcpy/info")
    async def scrcpy_info() -> dict[str, Any]:
        meta = service.meta
        return {
            "state": service.state.value,
            "device": {
                "name": meta.device_name if meta else None,
                "width": meta.width if meta else None,
                "height": meta.height if meta else None,
                "video_codec": meta.video_codec.value if meta and meta.video_codec else None,
                "audio_codec": meta.audio_codec.value if meta and meta.audio_codec else None,
            },
            "config": {
                "max_size": service.config.max_size,
                "max_fps": service.config.max_fps,
                "video_bit_rate": service.config.video_bit_rate,
                "audio_bit_rate": service.config.audio_bit_rate,
                "video_codec": service.config.video_codec.value,
                "audio_codec": service.config.audio_codec.value,
                "audio_enabled": service.config.audio,
                "control_enabled": service.config.control,
            },
        }

    @router.websocket("/ws/scrcpy")
    async def ws_scrcpy(ws: WebSocket) -> None:
        await ws.accept()
        loop = asyncio.get_running_loop()
        receive_audio = ws.query_params.get("audio", "1").lower() not in {
            "0",
            "false",
            "no",
        }
        try:
            await _send_init_message(ws, service)
        except Exception:
            await ws.close()
            return

        subscription = service.subscribe(loop=loop, receive_audio=receive_audio)
        send_task = asyncio.create_task(_pump_packets(ws, subscription))
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("ws/scrcpy received invalid JSON: %r", raw)
                    continue
                try:
                    control = _parse_control_json(message)
                except (KeyError, ValueError) as exc:
                    log.warning("ws/scrcpy bad control message %s: %s", message, exc)
                    continue
                try:
                    service.send_control_message(control)
                except AndroidStreamError as exc:
                    log.warning("scrcpy control send failed: %s", exc)
                    await ws.send_text(
                        json.dumps({"type": "error", "message": str(exc)})
                    )
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("ws/scrcpy receive loop crashed")
        finally:
            subscription.close()
            send_task.cancel()
            try:
                await send_task
            except (asyncio.CancelledError, Exception):
                pass

    return router


async def _send_init_message(ws: WebSocket, service: ScrcpyV4Service) -> None:
    meta = service.meta
    payload = {
        "type": "init",
        "state": service.state.value,
        "device": {
            "name": meta.device_name if meta else None,
            "width": meta.width if meta else None,
            "height": meta.height if meta else None,
            "video_codec": meta.video_codec.value if meta and meta.video_codec else None,
            "audio_codec": meta.audio_codec.value if meta and meta.audio_codec else None,
        },
        "binary_header": {
            "size": HEADER_SIZE,
            "kinds": {"video": KIND_VIDEO, "audio": KIND_AUDIO, "session": KIND_SESSION},
            "flags": {
                "config": 0x01,
                "key_frame": 0x02,
                "client_resized": 0x04,
            },
        },
    }
    await ws.send_text(json.dumps(payload))


async def _pump_packets(ws: WebSocket, subscription) -> None:
    try:
        async for packet in subscription:
            await ws.send_bytes(_encode_packet(packet))
    except WebSocketDisconnect:
        return
    except Exception:
        log.exception("ws/scrcpy packet pump crashed")


# ---------------------------------------------------------------------------
# Standalone app helper (for `uvicorn ... :app`)
# ---------------------------------------------------------------------------


def create_scrcpy_v4_app(config: ScrcpyV4Config | None = None) -> FastAPI:
    """Build a minimal FastAPI app exposing only the v4 bridge.

    Useful for running the new pipeline standalone without dragging in the
    legacy WebRTC/JPEG pathway.
    """
    service = ScrcpyV4Service(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        service.bind_loop(loop)
        # Start scrcpy lazily when the first WebSocket connects? For now we
        # eagerly start so the first browser hit is fast and errors surface
        # in `uvicorn` logs immediately.
        try:
            service.start()
        except Exception:
            log.exception("scrcpy v4 service failed to start during lifespan")
        try:
            yield
        finally:
            service.stop()

    app = FastAPI(title="android-stream-scrcpy-v4", version="0.2.0", lifespan=lifespan)
    app.include_router(create_scrcpy_v4_router(service))
    return app
