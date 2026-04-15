from __future__ import annotations

import base64

import numpy as np

from android_stream.frame_pipeline import bgr_to_base64, bgr_to_jpeg_bytes, packet_from_bgr, save_bgr_as_jpeg


def make_test_frame() -> np.ndarray:
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    frame[:, :] = (10, 120, 240)
    return frame


def test_bgr_to_jpeg_and_base64_roundtrip() -> None:
    frame = make_test_frame()
    jpeg = bgr_to_jpeg_bytes(frame)
    assert isinstance(jpeg, bytes)
    assert len(jpeg) > 0

    b64 = bgr_to_base64(frame)
    assert isinstance(b64, str)
    assert base64.b64decode(b64)


def test_packet_metadata_and_save(tmp_path) -> None:
    frame = make_test_frame()
    packet = packet_from_bgr(frame, timestamp_ms=12345)
    assert packet.timestamp_ms == 12345
    assert packet.width == 16
    assert packet.height == 16

    file_path = save_bgr_as_jpeg(frame, tmp_path / "frame.jpg")
    assert file_path.exists()
    assert file_path.stat().st_size > 0


def test_packet_jpeg_cache_respects_quality() -> None:
    frame = make_test_frame()
    packet = packet_from_bgr(frame)
    jpeg_q30 = packet.to_jpeg_bytes(quality=30)
    jpeg_q90 = packet.to_jpeg_bytes(quality=90)
    assert jpeg_q30 != jpeg_q90

    # 同质量走缓存，返回内容稳定一致。
    jpeg_q30_again = packet.to_jpeg_bytes(quality=30)
    assert jpeg_q30_again == jpeg_q30
