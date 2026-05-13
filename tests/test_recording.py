from __future__ import annotations

import numpy as np
import pytest

from android_stream.frame_types import FramePacket
from android_stream.recording import RecordingManager


def make_packet(width: int = 32, height: int = 24, timestamp_ms: int = 1000) -> FramePacket:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = (20, 120, 220)
    return FramePacket.from_bgr(frame, timestamp_ms=timestamp_ms)


def test_recording_manager_writes_mp4_file(tmp_path) -> None:
    manager = RecordingManager()
    started = manager.start(
        output_dir=tmp_path,
        session_id="session/one",
        serial="device:5555",
        codec="mpeg4",
        fps=5,
        now_ms=1000,
    )

    manager.write(make_packet(timestamp_ms=1000))
    manager.write(make_packet(timestamp_ms=1200))
    result = manager.stop(now_ms=1400)

    assert result.path == started.path
    assert result.frame_count == 2
    assert result.duration_ms == 400
    assert result.path is not None
    assert result.path.exists()
    assert result.path.suffix == ".mp4"
    assert result.path.stat().st_size > 0
    assert "/" not in result.path.name
    assert ":" not in result.path.name


def test_recording_manager_rejects_duplicate_start(tmp_path) -> None:
    manager = RecordingManager()
    manager.start(output_dir=tmp_path, session_id="s1", serial="d1")

    with pytest.raises(RuntimeError, match="recording already active"):
        manager.start(output_dir=tmp_path, session_id="s2", serial="d1")


def test_recording_manager_handles_empty_recording(tmp_path) -> None:
    manager = RecordingManager()
    manager.start(output_dir=tmp_path, session_id="s1", serial="d1", now_ms=1000)

    result = manager.stop(now_ms=1500)

    assert result.frame_count == 0
    assert result.duration_ms == 500
    assert result.path is None


def test_recording_manager_rejects_size_changes(tmp_path) -> None:
    manager = RecordingManager()
    manager.start(output_dir=tmp_path, session_id="s1", serial="d1")
    manager.write(make_packet(width=32, height=24))

    with pytest.raises(RuntimeError, match="frame size changed"):
        manager.write(make_packet(width=64, height=24))
    assert manager.active is False
