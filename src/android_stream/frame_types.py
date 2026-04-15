from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass(slots=True)
class FramePacket:
    timestamp_ms: int
    width: int
    height: int
    bgr_frame: np.ndarray
    _jpeg_cache: dict[int, bytes] = field(default_factory=dict, repr=False)

    @classmethod
    def from_bgr(cls, frame: np.ndarray, timestamp_ms: int | None = None) -> "FramePacket":
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("frame must be a BGR image with shape (H, W, 3)")
        ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
        height, width = frame.shape[:2]
        return cls(timestamp_ms=ts, width=width, height=height, bgr_frame=frame)

    def to_jpeg_bytes(self, quality: int = 90) -> bytes:
        quality = int(quality)
        if quality in self._jpeg_cache:
            return self._jpeg_cache[quality]

        rgb = self.bgr_frame[:, :, ::-1]
        image = Image.fromarray(rgb, mode="RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality)
        jpeg = output.getvalue()
        self._jpeg_cache[quality] = jpeg
        return jpeg

    def to_base64(self, quality: int = 90) -> str:
        return base64.b64encode(self.to_jpeg_bytes(quality=quality)).decode("ascii")

    def save_jpeg(self, path: str | Path, quality: int = 90) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self.to_jpeg_bytes(quality=quality))
        return output_path
