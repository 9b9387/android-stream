from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

import av
from aiortc import RTCPeerConnection, RTCRtpSender, RTCSessionDescription, VideoStreamTrack
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from android_stream.frame_types import FramePacket
from android_stream.models import StreamState
from android_stream.recording import RecordingManager, RecordingResult
from android_stream.sdk import AndroidStreamSDK, StreamConfig

log = logging.getLogger(__name__)


@dataclass(slots=True)
class WebStreamConfig:
    # 默认把长边限制到 720，避免浏览器端画面过大。
    stream: StreamConfig = field(default_factory=lambda: StreamConfig(max_size=720))
    jpeg_quality: int = 80
    snapshot_fps: int = 5
    wait_timeout_s: float = 10.0
    recording_output_dir: Path | None = None
    start_stream_on_lifespan: bool = True


class FrameBroadcaster:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._condition = asyncio.Condition()
        self._sequence = 0
        self._latest_payload: Any | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish_from_thread(self, payload: Any) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._publish_in_loop, payload)

    async def wait_for_next(self, last_sequence: int, timeout_s: float) -> tuple[int, Any]:
        async with self._condition:
            await asyncio.wait_for(
                self._condition.wait_for(lambda: self._sequence > last_sequence),
                timeout=timeout_s,
            )
            assert self._latest_payload is not None
            return self._sequence, self._latest_payload

    def _publish_in_loop(self, payload: Any) -> None:
        self._latest_payload = payload
        self._sequence += 1

        async def _notify() -> None:
            async with self._condition:
                self._condition.notify_all()

        asyncio.create_task(_notify())


class SnapshotCache:
    def __init__(self, fps: int = 5) -> None:
        self._min_interval_ms = int(1000 / max(1, int(fps)))
        self._latest: FramePacket | None = None
        self._last_update_ms: int | None = None
        self._lock = threading.Lock()

    def update(self, packet: FramePacket) -> bool:
        with self._lock:
            if (
                self._last_update_ms is not None
                and packet.timestamp_ms - self._last_update_ms < self._min_interval_ms
            ):
                return False
            self._latest = packet
            self._last_update_ms = packet.timestamp_ms
            return True

    def latest(self) -> FramePacket | None:
        with self._lock:
            return self._latest


class LatestFrameVideoTrack(VideoStreamTrack):
    def __init__(self, broadcaster: FrameBroadcaster, *, wait_timeout_s: float) -> None:
        super().__init__()
        self._broadcaster = broadcaster
        self._sequence = 0
        self._wait_timeout_s = wait_timeout_s

    async def recv(self) -> av.VideoFrame:
        while True:
            try:
                self._sequence, packet = await self._broadcaster.wait_for_next(
                    self._sequence,
                    timeout_s=self._wait_timeout_s,
                )
                break
            except (TimeoutError, asyncio.TimeoutError):
                continue

        if not isinstance(packet, FramePacket):
            raise RuntimeError("webrtc frame broadcaster received non-frame payload")
        pts, time_base = await self.next_timestamp()
        frame = av.VideoFrame.from_ndarray(packet.bgr_frame, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame


class RecordingStartRequest(BaseModel):
    session_id: str = Field(min_length=1)
    serial: str | None = None
    codec: str | None = None
    fps: int | None = Field(default=None, ge=1, le=60)


class WebRTCOfferRequest(BaseModel):
    sdp: str
    type: str


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


def snapshot_download_filename() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    return f"snapshot-{timestamp}.jpg"


def create_web_app(config: WebStreamConfig | None = None) -> FastAPI:
    app_config = config or WebStreamConfig()
    sdk = AndroidStreamSDK(app_config.stream)
    broadcaster = FrameBroadcaster()
    packet_broadcaster = FrameBroadcaster()
    snapshot_cache = SnapshotCache(fps=app_config.snapshot_fps)
    recorder = RecordingManager()
    active_ws: set[WebSocket] = set()
    active_peer_connections: set[RTCPeerConnection] = set()
    loop_holder: list[asyncio.AbstractEventLoop | None] = [None]
    supervisor_lock = asyncio.Lock()
    consecutive_failures = 0

    unsubscribe_frame = None
    unsubscribe_error = None
    unsubscribe_state = None

    def configured_recording_dir() -> Path:
        return app_config.recording_output_dir or default_recording_dir()

    def on_frame(packet) -> None:
        if recorder.active:
            try:
                recorder.write(packet)
            except Exception:
                log.exception("android-stream recording write failed")
        snapshot_cache.update(packet)
        packet_broadcaster.publish_from_thread(packet)
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

    async def close_all_peer_connections() -> None:
        peers = list(active_peer_connections)
        active_peer_connections.clear()
        if peers:
            await asyncio.gather(*(pc.close() for pc in peers), return_exceptions=True)

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
        packet_broadcaster.bind_loop(loop)
        consecutive_failures = 0
        unsubscribe_frame = sdk.on_frame(on_frame)
        unsubscribe_error = sdk.on_error(on_error)
        unsubscribe_state = sdk.on_state_change(on_state_change)
        if app_config.start_stream_on_lifespan:
            sdk.start()
        try:
            yield
        finally:
            loop_holder[0] = None
            await close_all_websockets(1001)
            await close_all_peer_connections()
            if app_config.start_stream_on_lifespan:
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
    app.state.latest_recording_path = None
    app.state.snapshot_cache = snapshot_cache
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        ],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
    )

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
        app.state.latest_recording_path = None
        result = recorder.start(
            output_dir=configured_recording_dir(),
            session_id=request.session_id,
            serial=request.serial or app_config.stream.device_serial,
            codec=request.codec or os.getenv("ANDROID_STREAM_RECORDING_CODEC", "mpeg4"),
            fps=request.fps or app_config.stream.max_fps or 10,
        )
        payload = recording_result_payload(result)
        payload["output_dir"] = str(configured_recording_dir())
        return payload

    @app.post("/recordings/stop")
    async def recordings_stop() -> dict[str, Any]:
        result = recorder.stop()
        if result.path is not None and result.path.exists():
            app.state.latest_recording_path = result.path
        payload = recording_result_payload(result)
        payload["output_dir"] = str(configured_recording_dir())
        if app.state.latest_recording_path is not None:
            payload["download_url"] = "/recordings/download/latest"
        return payload

    @app.get("/recordings/status")
    async def recordings_status() -> dict[str, Any]:
        result = recorder.status()
        if result is None:
            payload: dict[str, Any] = {
                "ok": True,
                "active": False,
                "output_dir": str(configured_recording_dir()),
            }
            if app.state.latest_recording_path is not None:
                payload["download_url"] = "/recordings/download/latest"
            return payload
        payload = recording_result_payload(result)
        payload["active"] = True
        payload["output_dir"] = str(configured_recording_dir())
        return payload

    @app.get("/recordings/download/latest")
    async def recordings_download_latest() -> FileResponse:
        path = app.state.latest_recording_path
        if path is None or not Path(path).exists():
            raise HTTPException(status_code=404, detail="no completed recording available")
        path = Path(path)
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.get("/snapshot")
    async def snapshot(quality: int = 90) -> Response:
        packet = snapshot_cache.latest()
        if packet is None:
            raise HTTPException(status_code=404, detail="no frame available yet")
        return Response(
            content=packet.to_jpeg_bytes(quality=quality),
            media_type="image/jpeg",
            headers={
                "Content-Disposition": f'attachment; filename="{snapshot_download_filename()}"'
            },
        )

    @app.post("/webrtc/offer")
    async def webrtc_offer(request: WebRTCOfferRequest) -> dict[str, str]:
        pc = RTCPeerConnection()
        active_peer_connections.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                active_peer_connections.discard(pc)
                await pc.close()

        track = LatestFrameVideoTrack(packet_broadcaster, wait_timeout_s=app_config.wait_timeout_s)
        transceiver = pc.addTransceiver(track, direction="sendonly")
        codecs = RTCRtpSender.getCapabilities("video").codecs
        h264_codecs = [codec for codec in codecs if codec.mimeType.lower() == "video/h264"]
        if h264_codecs:
            transceiver.setCodecPreferences(
                h264_codecs + [codec for codec in codecs if codec.mimeType.lower() != "video/h264"]
            )

        offer = RTCSessionDescription(sdp=request.sdp, type=request.type)
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

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
