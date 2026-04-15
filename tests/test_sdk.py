from __future__ import annotations

import threading

import numpy as np

from android_stream.frame_types import FramePacket
from android_stream.models import StreamState
from android_stream.sdk import AndroidStreamSDK, StreamConfig


class FakeBackend:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self._frame_handler = None
        self._error_handler = None

    def start(self, frame_handler, error_handler) -> None:
        self.started = True
        self._frame_handler = frame_handler
        self._error_handler = error_handler

    def stop(self) -> None:
        self.stopped = True

    def emit_frame(self, packet: FramePacket) -> None:
        if self._frame_handler is not None:
            self._frame_handler(packet)


def test_sdk_wait_for_first_frame_and_save(tmp_path) -> None:
    sdk = AndroidStreamSDK(StreamConfig())
    backend = FakeBackend()
    sdk._service._set_backend_for_test(backend)
    sdk.start()
    assert backend.started

    frame = np.zeros((10, 12, 3), dtype=np.uint8)
    backend.emit_frame(FramePacket.from_bgr(frame))
    first = sdk.wait_for_first_frame(timeout_s=1.0)
    assert first.width == 12
    assert first.height == 10

    out = sdk.save_latest_frame(tmp_path / "latest.jpg")
    assert out.exists()
    assert out.stat().st_size > 0
    sdk.stop()
    assert backend.stopped


def test_sdk_on_frame_subscription() -> None:
    sdk = AndroidStreamSDK(StreamConfig())
    backend = FakeBackend()
    sdk._service._set_backend_for_test(backend)
    called = threading.Event()

    def on_frame(_packet: FramePacket) -> None:
        called.set()

    unsubscribe = sdk.on_frame(on_frame)
    sdk.start()
    backend.emit_frame(FramePacket.from_bgr(np.zeros((5, 5, 3), dtype=np.uint8)))
    assert called.wait(timeout=1.0)
    unsubscribe()
    sdk.stop()


def test_sdk_reusable_after_context_manager() -> None:
    sdk = AndroidStreamSDK(StreamConfig())
    backend = FakeBackend()
    sdk._service._set_backend_for_test(backend)
    with sdk:
        backend.emit_frame(FramePacket.from_bgr(np.zeros((6, 7, 3), dtype=np.uint8)))
        got = sdk.wait_for_first_frame(timeout_s=1.0)
        assert got.width == 7

    # 再次启动应仍能更新 latest_frame，不应因内部订阅被解绑而失效。
    sdk.start()
    backend.emit_frame(FramePacket.from_bgr(np.zeros((8, 9, 3), dtype=np.uint8)))
    got2 = sdk.wait_for_first_frame(timeout_s=1.0)
    assert got2.width == 9
    sdk.stop()


def test_sdk_state_and_stats_exposed() -> None:
    sdk = AndroidStreamSDK(StreamConfig())
    backend = FakeBackend()
    sdk._service._set_backend_for_test(backend)
    states: list[StreamState] = []
    sdk.on_state_change(lambda s: states.append(s))
    sdk.start()
    backend.emit_frame(FramePacket.from_bgr(np.zeros((3, 3, 3), dtype=np.uint8)))
    assert sdk.stats.frames_received >= 1
    sdk.stop()
    assert sdk.state == StreamState.STOPPED
    assert StreamState.RUNNING in states


def test_sdk_stream_config_max_size_passed() -> None:
    sdk = AndroidStreamSDK(StreamConfig(max_size=720))
    assert sdk._service._backend.max_size == 720


def test_sdk_start_idempotent_does_not_reset_first_frame_event() -> None:
    sdk = AndroidStreamSDK(StreamConfig())
    backend = FakeBackend()
    sdk._service._set_backend_for_test(backend)
    sdk.start()
    backend.emit_frame(FramePacket.from_bgr(np.zeros((4, 4, 3), dtype=np.uint8)))
    first = sdk.wait_for_first_frame(timeout_s=1.0)
    assert first.width == 4

    # 再次调用 start（service 已运行）不应清空首帧事件。
    sdk.start()
    second = sdk.wait_for_first_frame(timeout_s=0.05)
    assert second is not None
    sdk.stop()
