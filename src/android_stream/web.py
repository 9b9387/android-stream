from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from android_stream.models import StreamState
from android_stream.recording import RecordingManager, RecordingResult
from android_stream.sdk import AndroidStreamSDK, StreamConfig

log = logging.getLogger(__name__)


@dataclass(slots=True)
class WebStreamConfig:
    # 默认把长边限制到 720，避免浏览器端画面过大。
    stream: StreamConfig = field(default_factory=lambda: StreamConfig(max_size=720))
    jpeg_quality: int = 80
    wait_timeout_s: float = 10.0


class FrameBroadcaster:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._condition = asyncio.Condition()
        self._sequence = 0
        self._latest_payload: bytes | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish_from_thread(self, payload: bytes) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._publish_in_loop, payload)

    async def wait_for_next(self, last_sequence: int, timeout_s: float) -> tuple[int, bytes]:
        async with self._condition:
            await asyncio.wait_for(
                self._condition.wait_for(lambda: self._sequence > last_sequence),
                timeout=timeout_s,
            )
            assert self._latest_payload is not None
            return self._sequence, self._latest_payload

    def _publish_in_loop(self, payload: bytes) -> None:
        self._latest_payload = payload
        self._sequence += 1

        async def _notify() -> None:
            async with self._condition:
                self._condition.notify_all()

        asyncio.create_task(_notify())


class RecordingStartRequest(BaseModel):
    output_dir: str | None = None
    session_id: str = Field(min_length=1)
    serial: str | None = None
    codec: str | None = None
    fps: int | None = Field(default=None, ge=1, le=60)


def default_recording_dir() -> Path:
    explicit = os.getenv("OMNI_FLOW_RECORDINGS_DIR")
    if explicit:
        return Path(explicit)
    data_dir = os.getenv("OMNI_FLOW_DATA_DIR")
    base = Path(data_dir) if data_dir else Path.cwd() / ".data"
    return base / "recordings"


def recording_result_payload(result: RecordingResult) -> dict[str, Any]:
    return {
        "ok": True,
        "path": str(result.path) if result.path is not None else None,
        "session_id": result.session_id,
        "serial": result.serial,
        "started_at_ms": result.started_at_ms,
        "finished_at_ms": result.finished_at_ms,
        "frame_count": result.frame_count,
        "duration_ms": result.duration_ms,
    }


def create_web_app(config: WebStreamConfig | None = None) -> FastAPI:
    app_config = config or WebStreamConfig()
    sdk = AndroidStreamSDK(app_config.stream)
    broadcaster = FrameBroadcaster()
    recorder = RecordingManager()
    active_ws: set[WebSocket] = set()
    loop_holder: list[asyncio.AbstractEventLoop | None] = [None]
    supervisor_lock = asyncio.Lock()
    consecutive_failures = 0

    unsubscribe_frame = None
    unsubscribe_error = None
    unsubscribe_state = None

    def on_frame(packet) -> None:
        if recorder.active:
            try:
                recorder.write(packet)
            except Exception:
                log.exception("android-stream recording write failed")
        broadcaster.publish_from_thread(packet.to_jpeg_bytes(quality=app_config.jpeg_quality))

    async def broadcast_state_json(state: StreamState) -> None:
        msg = json.dumps({"type": "state", "state": state.value})
        for ws in list(active_ws):
            try:
                await ws.send_text(msg)
            except Exception:
                active_ws.discard(ws)

    async def broadcast_error_json(message: str) -> None:
        msg = json.dumps({"type": "error", "message": message})
        for ws in list(active_ws):
            try:
                await ws.send_text(msg)
            except Exception:
                active_ws.discard(ws)

    async def close_all_websockets(code: int = 1011) -> None:
        for ws in list(active_ws):
            try:
                await ws.close(code=code)
            except Exception:
                pass
            active_ws.discard(ws)

    async def do_sdk_recover() -> None:
        nonlocal consecutive_failures
        async with supervisor_lock:
            await asyncio.sleep(1.0)
            try:
                sdk.stop()
            except Exception:
                log.exception("sdk.stop during supervisor recovery")
            try:
                sdk.start()
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                log.warning(
                    "android-stream supervisor restart failed (%s/%s): %s",
                    consecutive_failures,
                    3,
                    exc,
                )
                if consecutive_failures >= 3:
                    log.error("android-stream supervisor giving up; exiting process")
                    os._exit(1)

    def on_error(exc: Exception) -> None:
        loop = loop_holder[0]
        if loop is None:
            return

        def kick() -> None:
            asyncio.create_task(broadcast_error_json(str(exc)))

        loop.call_soon_threadsafe(kick)

    def on_state_change(state: StreamState) -> None:
        loop = loop_holder[0]
        if loop is None:
            return

        def kick() -> None:
            asyncio.create_task(broadcast_state_json(state))
            if state == StreamState.ERROR:
                asyncio.create_task(close_all_websockets(1011))
                asyncio.create_task(do_sdk_recover())

        loop.call_soon_threadsafe(kick)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal unsubscribe_frame, unsubscribe_error, unsubscribe_state, consecutive_failures
        loop = asyncio.get_running_loop()
        loop_holder[0] = loop
        broadcaster.bind_loop(loop)
        consecutive_failures = 0
        unsubscribe_frame = sdk.on_frame(on_frame)
        unsubscribe_error = sdk.on_error(on_error)
        unsubscribe_state = sdk.on_state_change(on_state_change)
        sdk.start()
        try:
            yield
        finally:
            loop_holder[0] = None
            await close_all_websockets(1001)
            sdk.stop()
            if unsubscribe_frame is not None:
                unsubscribe_frame()
                unsubscribe_frame = None
            if unsubscribe_error is not None:
                unsubscribe_error()
                unsubscribe_error = None
            if unsubscribe_state is not None:
                unsubscribe_state()
                unsubscribe_state = None

    app = FastAPI(title="android-stream-web", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "state": sdk.state.value,
            "stats": {
                "frames_received": sdk.stats.frames_received,
                "frames_dropped": sdk.stats.frames_dropped,
                "errors": sdk.stats.errors,
            },
        }

    @app.post("/recordings/start")
    async def recordings_start(request: RecordingStartRequest) -> dict[str, Any]:
        result = recorder.start(
            output_dir=request.output_dir or default_recording_dir(),
            session_id=request.session_id,
            serial=request.serial or app_config.stream.device_serial,
            codec=request.codec or os.getenv("ANDROID_STREAM_RECORDING_CODEC", "mpeg4"),
            fps=request.fps or app_config.stream.max_fps or 10,
        )
        return recording_result_payload(result)

    @app.post("/recordings/stop")
    async def recordings_stop() -> dict[str, Any]:
        result = recorder.stop()
        return recording_result_payload(result)

    @app.get("/recordings/status")
    async def recordings_status() -> dict[str, Any]:
        result = recorder.status()
        if result is None:
            return {"ok": True, "active": False}
        payload = recording_result_payload(result)
        payload["active"] = True
        return payload

    @app.websocket("/ws/stream")
    async def ws_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        active_ws.add(websocket)
        try:
            await websocket.send_text(json.dumps({"type": "state", "state": sdk.state.value}))
            sequence = 0
            while True:
                try:
                    sequence, payload = await broadcaster.wait_for_next(
                        sequence, timeout_s=app_config.wait_timeout_s
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    # A static screen may not produce a frame for a while. Keep
                    # the websocket alive instead of making clients treat it as
                    # an upstream stream failure.
                    await websocket.send_text(json.dumps({"type": "state", "state": sdk.state.value}))
                    continue
                await websocket.send_bytes(payload)
        except WebSocketDisconnect:
            return
        finally:
            active_ws.discard(websocket)

    return app


app = create_web_app()
