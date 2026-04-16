from __future__ import annotations

import asyncio

from android_stream.web import FrameBroadcaster, WebStreamConfig, create_web_app


def test_web_app_routes_present() -> None:
    app = create_web_app()
    paths = {route.path for route in app.routes}
    assert "/health" in paths
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
