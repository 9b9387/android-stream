from __future__ import annotations

from typing import Callable, Protocol

from android_stream.frame_types import FramePacket


FrameHandler = Callable[[FramePacket], None]
ErrorHandler = Callable[[Exception], None]


class FrameBackend(Protocol):
    def start(self, frame_handler: FrameHandler, error_handler: ErrorHandler) -> None:
        ...

    def stop(self) -> None:
        ...
