from __future__ import annotations

import asyncio
import json
import struct
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from android_stream.models import StreamState
from android_stream.scrcpy_v4 import (
    AudioCodec,
    ScrcpyV4Config,
    ScrcpyV4Service,
    VideoCodec,
)
from android_stream.scrcpy_v4.backend import StreamMeta
from android_stream.scrcpy_v4.protocol import (
    ACTION_DOWN,
    BUTTON_PRIMARY,
    CONTROL_MSG_TYPE,
    POINTER_ID_MOUSE,
    ControlMessage,
)
from android_stream.scrcpy_v4.service import MediaKind, MediaPacket
from android_stream.scrcpy_v4.web import (
    HEADER_SIZE,
    KIND_AUDIO,
    KIND_SESSION,
    KIND_VIDEO,
    _encode_packet,
    _parse_control_json,
    create_scrcpy_v4_router,
)
from android_stream.web import WebStreamConfig, create_web_app


# ---------------------------------------------------------------------------
# Encoded packet binary frame layout (the cross-cutting client/server contract)
# ---------------------------------------------------------------------------


class TestEncodePacket:
    def test_video_packet_header_layout(self) -> None:
        payload = b"\x00\x00\x00\x01\x65hello"
        packet = MediaPacket(
            kind=MediaKind.VIDEO,
            pts_us=0xDEAD_BEEF,
            config=False,
            key_frame=True,
            payload=payload,
        )
        wire = _encode_packet(packet)
        assert len(wire) == HEADER_SIZE + len(payload)
        kind, flags, _reserved, size, pts = struct.unpack(">BBHIQ", wire[:HEADER_SIZE])
        assert kind == KIND_VIDEO
        assert flags == 0x02  # key frame, no config
        assert size == len(payload)
        assert pts == 0xDEAD_BEEF
        assert wire[HEADER_SIZE:] == payload

    def test_audio_config_packet_sets_config_flag(self) -> None:
        payload = b"OpusHead\x01\x01"
        packet = MediaPacket(
            kind=MediaKind.AUDIO,
            pts_us=0,
            config=True,
            key_frame=False,
            payload=payload,
        )
        wire = _encode_packet(packet)
        kind, flags, _, size, _ = struct.unpack(">BBHIQ", wire[:HEADER_SIZE])
        assert kind == KIND_AUDIO
        assert flags & 0x01  # config flag is bit 0
        assert size == len(payload)

    def test_session_packet_packs_dimensions_into_pts_field(self) -> None:
        # Browser-side parsing of session packets recovers width/height by
        # reinterpreting the 64-bit "pts" as two big-endian uint32s. Drift
        # here would silently break canvas sizing, so we assert the layout
        # explicitly.
        packet = MediaPacket(
            kind=MediaKind.SESSION,
            pts_us=0,
            config=False,
            key_frame=False,
            payload=b"",
            width=720,
            height=1280,
            client_resized=True,
        )
        wire = _encode_packet(packet)
        assert len(wire) == HEADER_SIZE
        kind, flags, _, size, pts_packed = struct.unpack(">BBHIQ", wire)
        assert kind == KIND_SESSION
        assert size == 0
        assert flags & 0x04  # client_resized flag
        assert (pts_packed >> 32) & 0xFFFFFFFF == 720
        assert pts_packed & 0xFFFFFFFF == 1280


# ---------------------------------------------------------------------------
# Control message JSON parser
# ---------------------------------------------------------------------------


class TestParseControlJson:
    def test_touch_down_with_mouse_pointer_alias(self) -> None:
        msg = _parse_control_json(
            {
                "type": "touch",
                "action": "down",
                "pointerId": "mouse",
                "x": 100,
                "y": 200,
                "screenWidth": 720,
                "screenHeight": 1280,
            }
        )
        assert msg.type == CONTROL_MSG_TYPE.INJECT_TOUCH_EVENT
        assert msg.action == ACTION_DOWN
        assert msg.pointer_id == POINTER_ID_MOUSE
        assert msg.x == 100 and msg.y == 200
        assert msg.screen_width == 720 and msg.screen_height == 1280
        assert msg.buttons == BUTTON_PRIMARY

    def test_touch_up_defaults_buttons_to_zero(self) -> None:
        # On pointer-up the browser-side gesture is finished; we must release
        # all pressed buttons so the device sees a clean release event.
        msg = _parse_control_json(
            {
                "type": "touch",
                "action": "up",
                "pointerId": "mouse",
                "x": 1,
                "y": 1,
                "screenWidth": 720,
                "screenHeight": 1280,
            }
        )
        assert msg.buttons == 0

    def test_back_action_maps_to_back_or_screen_on(self) -> None:
        msg = _parse_control_json({"type": "back", "action": "down"})
        assert msg.type == CONTROL_MSG_TYPE.BACK_OR_SCREEN_ON
        assert msg.action == 0

    def test_text_message_passes_unicode_through(self) -> None:
        msg = _parse_control_json({"type": "text", "text": "你好"})
        assert msg.type == CONTROL_MSG_TYPE.INJECT_TEXT
        assert msg.text == "你好"

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_control_json({"type": "telepathy"})


# ---------------------------------------------------------------------------
# Service fan-out: subscribe before connect, and dropping under back-pressure
# ---------------------------------------------------------------------------


class TestServiceFanOut:
    def test_subscriber_receives_init_snapshot(self) -> None:
        async def _run() -> None:
            from android_stream.scrcpy_v4.protocol import SessionPacket

            service = ScrcpyV4Service(ScrcpyV4Config(audio=False))
            # Inject cached state instead of starting a real backend connection.
            service._meta = StreamMeta(
                device_name="Pixel Test",
                video_codec=VideoCodec.H264,
                audio_codec=None,
                width=720,
                height=1280,
            )
            service._video_config = b"sps+pps"
            service._latest_session = SessionPacket(
                width=720, height=1280, client_resized=False
            )

            loop = asyncio.get_running_loop()
            sub = service.subscribe(loop=loop, receive_audio=False)
            try:
                session = await asyncio.wait_for(sub._subscriber.queue.get(), timeout=1.0)
                video_config = await asyncio.wait_for(
                    sub._subscriber.queue.get(), timeout=1.0
                )
                assert session.kind == MediaKind.SESSION
                assert video_config.kind == MediaKind.VIDEO
                assert video_config.config is True
                assert video_config.payload == b"sps+pps"
            finally:
                sub.close()

        asyncio.run(_run())

    def test_subscriber_replays_cached_key_frame(self) -> None:
        # Regression: scrcpy v4 only emits an IDR right after the encoder
        # starts and then on ``i-frame-interval`` (10s default). On a static
        # screen the next IDR may never come. A subscriber that connects
        # *after* the initial IDR therefore needs to receive the cached one
        # in its bootstrap snapshot, otherwise the WebCodecs decoder hangs
        # waiting for a sync point. This was the Galaxy S21 + audio
        # reproduction we hit during 2-device testing.
        async def _run() -> None:
            from android_stream.scrcpy_v4.protocol import SessionPacket

            service = ScrcpyV4Service(
                ScrcpyV4Config(audio=False, control=False)
            )
            service._meta = StreamMeta(
                device_name="Pixel Test",
                video_codec=VideoCodec.H264,
                audio_codec=None,
                width=720,
                height=1280,
            )
            service._video_config = b"sps+pps"
            service._latest_session = SessionPacket(
                width=720, height=1280, client_resized=False
            )
            service._latest_key_frame = MediaPacket(
                kind=MediaKind.VIDEO,
                pts_us=12345,
                config=False,
                key_frame=True,
                payload=b"\x00\x00\x00\x01\x65idr-payload",
            )

            loop = asyncio.get_running_loop()
            sub = service.subscribe(loop=loop, receive_audio=False)
            try:
                packets: list[MediaPacket] = []
                for _ in range(3):
                    packets.append(
                        await asyncio.wait_for(
                            sub._subscriber.queue.get(), timeout=1.0
                        )
                    )

                kinds = [p.kind for p in packets]
                assert kinds == [
                    MediaKind.SESSION,
                    MediaKind.VIDEO,
                    MediaKind.VIDEO,
                ]
                assert packets[1].config is True
                assert packets[2].key_frame is True
                assert packets[2].config is False
                assert packets[2].payload == b"\x00\x00\x00\x01\x65idr-payload"
            finally:
                sub.close()

        asyncio.run(_run())

    def test_back_pressure_drops_oldest_non_config_packet(self) -> None:
        async def _run() -> None:
            service = ScrcpyV4Service(
                ScrcpyV4Config(audio=False, queue_max_packets=2)
            )
            loop = asyncio.get_running_loop()
            sub = service.subscribe(loop=loop, receive_audio=False)
            try:
                # Fill the subscriber queue: a config (sticky) packet first,
                # then a media packet. With ``queue_max_packets=2`` the next
                # producer push must evict the older media packet, never the
                # codec extradata.
                await sub._subscriber.queue.put(
                    MediaPacket(
                        kind=MediaKind.VIDEO,
                        pts_us=0,
                        config=True,
                        key_frame=False,
                        payload=b"cfg",
                    )
                )
                await sub._subscriber.queue.put(
                    MediaPacket(
                        kind=MediaKind.VIDEO,
                        pts_us=1,
                        config=False,
                        key_frame=True,
                        payload=b"old-frame",
                    )
                )

                # Mimic the call_soon_threadsafe path the backend producer
                # would take from one of its IO threads.
                done = threading.Event()

                def _from_thread() -> None:
                    service._enqueue(
                        sub._subscriber,
                        MediaPacket(
                            kind=MediaKind.VIDEO,
                            pts_us=2,
                            config=False,
                            key_frame=False,
                            payload=b"new-frame",
                        ),
                    )
                    done.set()

                await loop.run_in_executor(None, _from_thread)
                assert done.wait(0.5)
                # Allow the scheduled call_soon callback to run.
                await asyncio.sleep(0)

                packets: list[MediaPacket] = []
                while not sub._subscriber.queue.empty():
                    packets.append(sub._subscriber.queue.get_nowait())

                payloads = [p.payload for p in packets]
                assert b"cfg" in payloads, "config packet must survive back-pressure"
                assert b"new-frame" in payloads, "newest packet must be enqueued"
                assert (
                    b"old-frame" not in payloads
                ), "older non-config packet must be dropped"
                assert sub._subscriber.drops >= 1
            finally:
                sub.close()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Web app wiring (router only attached when scrcpy v4 is enabled)
# ---------------------------------------------------------------------------


def test_router_is_only_mounted_when_v4_enabled() -> None:
    enabled = create_web_app(
        WebStreamConfig(
            start_stream_on_lifespan=False,
            scrcpy_v4_enabled=True,
            scrcpy_v4_start_on_lifespan=False,
        )
    )
    paths = {route.path for route in enabled.routes}
    assert "/ws/scrcpy" in paths
    assert "/scrcpy/info" in paths

    disabled = create_web_app(WebStreamConfig(start_stream_on_lifespan=False))
    paths = {route.path for route in disabled.routes}
    assert "/ws/scrcpy" not in paths
    assert "/scrcpy/info" not in paths


def test_demo_page_exists_and_uses_webcodecs() -> None:
    html = (Path(__file__).parents[1] / "web_demo" / "scrcpy.html").read_text()
    assert "VideoDecoder" in html
    assert "AudioDecoder" in html
    assert "/ws/scrcpy" in html
    # We rely on the canvas overlay to capture pointer events so that the
    # video element does not steal them on touch devices.
    assert 'id="overlay"' in html
    assert "pointerdown" in html
    assert "rotate" in html  # rotate button issues a control message


# ---------------------------------------------------------------------------
# /scrcpy/info endpoint exposes service config without requiring a real device
# ---------------------------------------------------------------------------


def test_scrcpy_info_endpoint_returns_config() -> None:
    service = ScrcpyV4Service(
        ScrcpyV4Config(
            max_size=720,
            max_fps=30,
            video_codec=VideoCodec.H264,
            audio_codec=AudioCodec.OPUS,
        )
    )
    app = FastAPI()
    app.include_router(create_scrcpy_v4_router(service))

    with TestClient(app) as client:
        response = client.get("/scrcpy/info")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == StreamState.STOPPED.value
    assert body["config"]["max_size"] == 720
    assert body["config"]["video_codec"] == "h264"
    assert body["config"]["audio_codec"] == "opus"
