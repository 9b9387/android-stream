from __future__ import annotations

import time
from pathlib import Path

from android_stream.backends.scrcpy_proto_backend import SCRCPY_SERVER_SHA256, ScrcpyProtoBackend
from android_stream.exceptions import ChecksumMismatchError, HandshakeError, ServerDownloadError


class _DummyResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        import io

        return io.BytesIO(self.payload)

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_download_retry_raises_server_download_error(monkeypatch, tmp_path) -> None:
    backend = ScrcpyProtoBackend(download_retries=2, download_timeout_s=0.01)
    target = tmp_path / "server.jar"

    def raise_url_error(*_args, **_kwargs):
        raise TimeoutError("network timeout")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)
    try:
        backend._download_with_retry("https://example.com/scrcpy-server", target)
        assert False, "expected ServerDownloadError"
    except ServerDownloadError:
        assert not target.exists()


def test_ensure_server_jar_checksum_mismatch(monkeypatch, tmp_path) -> None:
    backend = ScrcpyProtoBackend(scrcpy_version="2.4")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setitem(SCRCPY_SERVER_SHA256, "2.4", "0" * 64)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: _DummyResponse(b"bad-binary"))

    try:
        backend._ensure_server_jar()
        assert False, "expected ChecksumMismatchError"
    except ChecksumMismatchError:
        cache_file = tmp_path / ".cache" / "android-stream" / "scrcpy-server-v2.4.jar"
        assert not cache_file.exists()


class _BlockingStream:
    def read(self, _n: int) -> bytes:
        time.sleep(0.2)
        return b""


class _ErrorStream:
    def read(self, _n: int) -> bytes:
        raise RuntimeError("boom")


def test_read_server_boot_log_timeout() -> None:
    backend = ScrcpyProtoBackend(deploy_timeout_s=0.05)
    backend._server_stream = _BlockingStream()
    try:
        backend._read_server_boot_log_with_timeout()
        assert False, "expected HandshakeError"
    except HandshakeError as exc:
        assert "timeout" in str(exc)


def test_read_server_boot_log_error() -> None:
    backend = ScrcpyProtoBackend(deploy_timeout_s=0.05)
    backend._server_stream = _ErrorStream()
    try:
        backend._read_server_boot_log_with_timeout()
        assert False, "expected HandshakeError"
    except HandshakeError as exc:
        assert "failed reading" in str(exc)
