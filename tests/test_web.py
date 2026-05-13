from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from android_stream.frame_types import FramePacket
from android_stream.web import FrameBroadcaster, SnapshotCache, WebStreamConfig, create_web_app


def test_web_app_routes_present() -> None:
    app = create_web_app()
    paths = {route.path for route in app.routes}
    assert "/health" in paths
    assert "/recordings/start" in paths
    assert "/recordings/stop" in paths
    assert "/recordings/status" in paths
    assert "/recordings/download/latest" in paths
    assert "/snapshot" in paths
    assert "/webrtc/offer" in paths
    assert "/ws/stream" in paths


def test_frame_broadcaster_publish_and_wait() -> None:
    async def _run() -> None:
        broadcaster = FrameBroadcaster()
        broadcaster.bind_loop(asyncio.get_running_loop())
        broadcaster.publish_from_thread(b"jpeg-bytes")
        seq, payload = await broadcaster.wait_for_next(0, timeout_s=1.0)
        assert seq == 1
        assert payload == b"jpeg-bytes"

    asyncio.run(_run())


def test_web_stream_config_default_max_size() -> None:
    config = WebStreamConfig()
    assert config.stream.max_size == 720
    assert config.snapshot_fps == 5


def test_snapshot_cache_throttles_to_configured_fps() -> None:
    cache = SnapshotCache(fps=5)
    first = FramePacket.from_bgr(np.zeros((4, 5, 3), dtype=np.uint8), timestamp_ms=1000)
    skipped = FramePacket.from_bgr(np.zeros((6, 7, 3), dtype=np.uint8), timestamp_ms=1100)
    second = FramePacket.from_bgr(np.zeros((8, 9, 3), dtype=np.uint8), timestamp_ms=1200)

    assert cache.update(first) is True
    assert cache.update(skipped) is False
    assert cache.update(second) is True
    assert cache.latest() is second


def test_snapshot_route_returns_latest_cached_frame() -> None:
    app = create_web_app(WebStreamConfig(start_stream_on_lifespan=False))
    packet = FramePacket.from_bgr(np.zeros((4, 5, 3), dtype=np.uint8))
    app.state.snapshot_cache.update(packet)

    with TestClient(app) as client:
        response = client.get("/snapshot?quality=80")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert re.fullmatch(
        r'attachment; filename="snapshot-\d{8}-\d{6}-\d{3}\.jpg"',
        response.headers["content-disposition"],
    )
    assert response.content.startswith(b"\xff\xd8")


def test_demo_page_exposes_recording_controls() -> None:
    html = (Path(__file__).parents[1] / "web_demo" / "index.html").read_text()

    assert 'id="recording-start"' in html
    assert 'id="recording-stop"' in html
    assert 'id="recording-status"' in html
    assert 'id="recording-download"' in html
    assert 'id="recording-output-dir"' not in html
    assert 'id="pick-recording-dir"' not in html
    assert "/recordings/start" in html
    assert "/recordings/stop" in html
    assert "/recordings/status" in html
    assert "/recordings/download/latest" in html
    assert "/webrtc/offer" in html
    assert "/snapshot" in html
    assert "RTCPeerConnection" in html
    assert 'id="screen-video"' in html
    assert "response.blob()" in html
    assert "URL.createObjectURL(blob)" in html
    assert "link.download = snapshotFilename()" in html
    assert "showDirectoryPicker" not in html
    assert "recordingOutputDir" not in html
    assert "output_dir:" not in html


def test_web_recording_config_uses_server_output_dir(tmp_path) -> None:
    config = WebStreamConfig(recording_output_dir=tmp_path, start_stream_on_lifespan=False)
    app = create_web_app(config)
    with TestClient(app) as client:
        response = client.post(
            "/recordings/start",
            json={"session_id": "s1", "output_dir": "/client/path/ignored"},
        )

    assert response.status_code == 200
    assert response.json()["path"].startswith(str(tmp_path))


def test_download_latest_recording_returns_server_file(tmp_path) -> None:
    recording_path = tmp_path / "recording.mp4"
    recording_path.write_bytes(b"mp4-bytes")
    app = create_web_app(WebStreamConfig(recording_output_dir=tmp_path, start_stream_on_lifespan=False))
    app.state.latest_recording_path = recording_path

    with TestClient(app) as client:
        response = client.get("/recordings/download/latest")

    assert response.status_code == 200
    assert response.content == b"mp4-bytes"
    assert response.headers["content-type"].startswith("video/mp4")


def test_recording_routes_allow_demo_page_cors_preflight() -> None:
    app = create_web_app()
    with TestClient(app) as client:
        response = client.options(
            "/recordings/start",
            headers={
                "Origin": "http://127.0.0.1:8080",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8080"
    assert "POST" in response.headers["access-control-allow-methods"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_websocket_sends_initial_state_control_frame() -> None:
    app = create_web_app(WebStreamConfig(start_stream_on_lifespan=False))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as ws:
            raw = ws.receive_text()
            data = json.loads(raw)
            assert data.get("type") == "state"
            assert data.get("state") in {"stopped", "starting", "running", "stopping", "error"}
