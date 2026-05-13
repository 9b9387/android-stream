from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av

from android_stream.frame_types import FramePacket


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(slots=True)
class RecordingResult:
    path: Path | None
    session_id: str
    serial: str | None
    started_at_ms: int
    finished_at_ms: int | None
    frame_count: int
    duration_ms: int


@dataclass(slots=True)
class _ActiveRecording:
    path: Path
    session_id: str
    serial: str | None
    codec: str
    fps: int
    started_at_ms: int
    frame_count: int = 0
    width: int | None = None
    height: int | None = None
    container: av.container.OutputContainer | None = None
    stream: av.video.stream.VideoStream | None = None


class RecordingManager:
    def __init__(self) -> None:
        self._active: _ActiveRecording | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active is not None

    def start(
        self,
        *,
        output_dir: str | Path,
        session_id: str,
        serial: str | None = None,
        codec: str = "mpeg4",
        fps: int = 10,
        now_ms: int | None = None,
    ) -> RecordingResult:
        with self._lock:
            if self._active is not None:
                raise RuntimeError("recording already active")
            started_at_ms = now_ms if now_ms is not None else _now_ms()
            output_path = self._build_output_path(
                Path(output_dir),
                session_id=session_id,
                serial=serial,
                started_at_ms=started_at_ms,
            )
            self._active = _ActiveRecording(
                path=output_path,
                session_id=session_id,
                serial=serial,
                codec=codec,
                fps=max(1, int(fps)),
                started_at_ms=started_at_ms,
            )
            return self._result(self._active, finished_at_ms=None)

    def write(self, packet: FramePacket) -> None:
        with self._lock:
            active = self._active
            if active is None:
                return
            if active.width is None or active.height is None:
                self._open_container(active, packet)
            elif active.width != packet.width or active.height != packet.height:
                self._active = None
                self._close_container(active)
                raise RuntimeError(
                    f"frame size changed during recording: "
                    f"{active.width}x{active.height} -> {packet.width}x{packet.height}"
                )

            assert active.container is not None
            assert active.stream is not None
            frame = av.VideoFrame.from_ndarray(packet.bgr_frame, format="bgr24")
            frame.pts = active.frame_count
            frame.time_base = Fraction(1, active.fps)
            for encoded in active.stream.encode(frame):
                active.container.mux(encoded)
            active.frame_count += 1

    def stop(self, *, now_ms: int | None = None) -> RecordingResult:
        with self._lock:
            active = self._active
            if active is None:
                raise RuntimeError("no active recording")
            self._active = None
            finished_at_ms = now_ms if now_ms is not None else _now_ms()
            self._close_container(active)
            return self._result(active, finished_at_ms=finished_at_ms)

    def status(self) -> RecordingResult | None:
        with self._lock:
            if self._active is None:
                return None
            return self._result(self._active, finished_at_ms=None)

    def _open_container(self, active: _ActiveRecording, packet: FramePacket) -> None:
        active.path.parent.mkdir(parents=True, exist_ok=True)
        container = av.open(str(active.path), mode="w")
        stream = container.add_stream(active.codec, rate=active.fps)
        stream.width = packet.width
        stream.height = packet.height
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, active.fps)
        active.width = packet.width
        active.height = packet.height
        active.container = container
        active.stream = stream

    @staticmethod
    def _close_container(active: _ActiveRecording) -> None:
        try:
            if active.container is not None and active.stream is not None:
                for encoded in active.stream.encode():
                    active.container.mux(encoded)
        finally:
            if active.container is not None:
                active.container.close()
                active.container = None
                active.stream = None

    @staticmethod
    def _build_output_path(
        output_dir: Path,
        *,
        session_id: str,
        serial: str | None,
        started_at_ms: int,
    ) -> Path:
        safe_session = _sanitize_name(session_id) or "session"
        safe_serial = _sanitize_name(serial or "device")
        return output_dir / f"agent-{safe_session}-{safe_serial}-{started_at_ms}.mp4"

    @staticmethod
    def _result(active: _ActiveRecording, *, finished_at_ms: int | None) -> RecordingResult:
        end_ms = finished_at_ms if finished_at_ms is not None else _now_ms()
        output_path = (
            active.path
            if finished_at_ms is None or (active.frame_count > 0 and active.path.exists())
            else None
        )
        return RecordingResult(
            path=output_path,
            session_id=active.session_id,
            serial=active.serial,
            started_at_ms=active.started_at_ms,
            finished_at_ms=finished_at_ms,
            frame_count=active.frame_count,
            duration_ms=max(0, end_ms - active.started_at_ms),
        )


def _sanitize_name(value: str) -> str:
    return _SAFE_NAME_RE.sub("_", value.strip())[:80].strip("._-")


def _now_ms() -> int:
    return int(time.time() * 1000)
