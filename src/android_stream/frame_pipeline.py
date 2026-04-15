from __future__ import annotations

from pathlib import Path

import numpy as np

from android_stream.frame_types import FramePacket


def packet_from_bgr(frame: np.ndarray, timestamp_ms: int | None = None) -> FramePacket:
    return FramePacket.from_bgr(frame=frame, timestamp_ms=timestamp_ms)


def bgr_to_jpeg_bytes(frame: np.ndarray, quality: int = 90) -> bytes:
    return FramePacket.from_bgr(frame=frame).to_jpeg_bytes(quality=quality)


def bgr_to_base64(frame: np.ndarray, quality: int = 90) -> str:
    return FramePacket.from_bgr(frame=frame).to_base64(quality=quality)


def save_bgr_as_jpeg(frame: np.ndarray, path: str | Path, quality: int = 90) -> Path:
    return FramePacket.from_bgr(frame=frame).save_jpeg(path=path, quality=quality)
