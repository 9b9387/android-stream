from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from android_stream.frame_types import FramePacket
from android_stream.models import StreamState, StreamStats
from android_stream.service import AndroidFrameService

OnFrameCallback = Callable[[FramePacket], None]
OnErrorCallback = Callable[[Exception], None]
OnStateCallback = Callable[[StreamState], None]


@dataclass(slots=True)
class StreamConfig:
    device_serial: str | None = None
    max_size: int = 0
    max_fps: int = 30
    bitrate: int = 8_000_000


class AndroidStreamSDK:
    def __init__(self, config: StreamConfig | None = None) -> None:
        self._config = config or StreamConfig()
        self._service = AndroidFrameService(
            device_serial=self._config.device_serial,
            max_size=self._config.max_size,
            max_fps=self._config.max_fps,
            bitrate=self._config.bitrate,
        )
        self._latest_frame: FramePacket | None = None
        self._latest_lock = threading.Lock()
        self._first_frame_event = threading.Event()
        self._service.on_frame(self._capture_latest)

    @property
    def running(self) -> bool:
        return self._service.running

    @property
    def state(self) -> StreamState:
        return self._service.state

    @property
    def stats(self) -> StreamStats:
        return self._service.stats

    @property
    def latest_frame(self) -> FramePacket | None:
        with self._latest_lock:
            return self._latest_frame

    def on_frame(self, callback: OnFrameCallback) -> Callable[[], None]:
        return self._service.on_frame(callback)

    def on_error(self, callback: OnErrorCallback) -> Callable[[], None]:
        return self._service.on_error(callback)

    def on_state_change(self, callback: OnStateCallback) -> Callable[[], None]:
        return self._service.on_state_change(callback)

    def start(self) -> None:
        if not self._service.running:
            self._first_frame_event.clear()
        self._service.start()

    def stop(self) -> None:
        self._service.stop()

    def wait_for_first_frame(self, timeout_s: float = 5.0) -> FramePacket:
        if not self._first_frame_event.wait(timeout=timeout_s):
            raise TimeoutError(f"timed out waiting for first frame in {timeout_s}s")
        latest = self.latest_frame
        if latest is None:
            raise RuntimeError("first frame event set but no frame available")
        return latest

    def save_latest_frame(self, path: str | Path, quality: int = 90) -> Path:
        latest = self.latest_frame
        if latest is None:
            raise RuntimeError("no frame available yet")
        return latest.save_jpeg(path, quality=quality)

    def _capture_latest(self, frame: FramePacket) -> None:
        with self._latest_lock:
            self._latest_frame = frame
        self._first_frame_event.set()

    def __enter__(self) -> "AndroidStreamSDK":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def create_client(config: StreamConfig | None = None) -> AndroidStreamSDK:
    return AndroidStreamSDK(config=config)
