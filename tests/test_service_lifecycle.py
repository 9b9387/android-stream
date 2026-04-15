from __future__ import annotations

import threading
import time

import numpy as np

from android_stream.backends.scrcpy_proto_backend import ScrcpyProtoBackend
from android_stream.frame_types import FramePacket
from android_stream.models import StreamState
from android_stream.service import AndroidFrameService


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

    def emit_error(self, exc: Exception) -> None:
        if self._error_handler is not None:
            self._error_handler(exc)


class FailStartBackend(FakeBackend):
    def start(self, frame_handler, error_handler) -> None:
        super().start(frame_handler, error_handler)
        raise RuntimeError("backend start failed")


def test_service_start_stop_and_on_frame() -> None:
    backend = FakeBackend()
    service = AndroidFrameService()
    service._set_backend_for_test(backend)
    got_frame = threading.Event()

    def callback(packet: FramePacket) -> None:
        assert packet.width == 8
        assert packet.height == 8
        got_frame.set()

    service.on_frame(callback)
    service.start()
    assert backend.started

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    backend.emit_frame(FramePacket.from_bgr(frame))

    assert got_frame.wait(timeout=1.0)
    service.stop()
    assert backend.stopped
    assert service.state == StreamState.STOPPED


def test_service_on_error_callback() -> None:
    backend = FakeBackend()
    service = AndroidFrameService()
    service._set_backend_for_test(backend)
    errors: list[str] = []

    service.on_error(lambda exc: errors.append(str(exc)))
    service.start()
    backend.emit_error(RuntimeError("boom"))

    end = time.time() + 1.0
    while time.time() < end and not errors:
        time.sleep(0.01)

    service.stop()
    assert errors and "boom" in errors[0]


def test_service_backend_selection_native_default() -> None:
    service = AndroidFrameService()
    assert isinstance(service._backend, ScrcpyProtoBackend)
    assert service._backend.max_size == 0


def test_service_max_size_passed_to_backend() -> None:
    service = AndroidFrameService(max_size=1080)
    assert isinstance(service._backend, ScrcpyProtoBackend)
    assert service._backend.max_size == 1080


def test_service_start_failure_rolls_back_state() -> None:
    service = AndroidFrameService()
    service._set_backend_for_test(FailStartBackend())
    try:
        service.start()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "backend start failed" in str(exc)

    # 启动失败后应回滚，不能残留半启动状态。
    assert service._running is False
    assert service._worker_thread is None
    assert service.state == StreamState.STOPPED


def test_service_state_change_callback_on_backend_error() -> None:
    backend = FakeBackend()
    service = AndroidFrameService()
    service._set_backend_for_test(backend)
    states: list[StreamState] = []
    service.on_state_change(lambda state: states.append(state))
    service.start()
    backend.emit_error(RuntimeError("disconnected"))
    end = time.time() + 1.0
    while time.time() < end and service.state != StreamState.ERROR:
        time.sleep(0.01)
    assert service.state == StreamState.ERROR
    assert StreamState.STARTING in states
    assert StreamState.RUNNING in states
    assert StreamState.ERROR in states
    service.stop()
    assert service.state == StreamState.STOPPED
