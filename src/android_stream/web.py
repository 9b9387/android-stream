from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from android_stream.sdk import AndroidStreamSDK, StreamConfig


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


def create_web_app(config: WebStreamConfig | None = None) -> FastAPI:
    app_config = config or WebStreamConfig()
    sdk = AndroidStreamSDK(app_config.stream)
    broadcaster = FrameBroadcaster()

    unsubscribe_frame = None
    unsubscribe_error = None

    def on_frame(packet) -> None:
        broadcaster.publish_from_thread(packet.to_jpeg_bytes(quality=app_config.jpeg_quality))

    def on_error(exc: Exception) -> None:
        # 无订阅方时忽略，状态可通过 /health 查看。
        _ = exc

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal unsubscribe_frame, unsubscribe_error
        broadcaster.bind_loop(asyncio.get_running_loop())
        unsubscribe_frame = sdk.on_frame(on_frame)
        unsubscribe_error = sdk.on_error(on_error)
        sdk.start()
        try:
            yield
        finally:
            sdk.stop()
            if unsubscribe_frame is not None:
                unsubscribe_frame()
            if unsubscribe_error is not None:
                unsubscribe_error()

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

    @app.websocket("/ws/stream")
    async def ws_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        sequence = 0
        try:
            while True:
                sequence, payload = await broadcaster.wait_for_next(
                    sequence, timeout_s=app_config.wait_timeout_s
                )
                await websocket.send_bytes(payload)
        except (WebSocketDisconnect, TimeoutError, asyncio.TimeoutError):
            return

    return app


app = create_web_app()
