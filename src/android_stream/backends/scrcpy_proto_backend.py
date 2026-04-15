from __future__ import annotations

import hashlib
import shutil
import socket
import struct
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import URLError

from adbutils import Network, adb
from av.codec import CodecContext
from av.error import InvalidDataError

from android_stream.backends.base import ErrorHandler, FrameHandler
from android_stream.exceptions import (
    ChecksumMismatchError,
    DecodeError,
    DeviceNotFoundError,
    HandshakeError,
    ServerDownloadError,
    StreamDisconnectedError,
)
from android_stream.frame_types import FramePacket

SCRCPY_SERVER_SHA256: dict[str, str] = {
    # 来源: https://github.com/Genymobile/scrcpy/releases/download/v2.4/SHA256SUMS.txt
    "2.4": "93c272b7438605c055e127f7444064ed78fa9ca49f81156777fd201e79ce7ba3",
}


@dataclass(slots=True)
class ScrcpyProtoBackend:
    device_serial: str | None = None
    max_size: int = 0
    max_fps: int = 30
    bitrate: int = 8_000_000
    scrcpy_version: str = "2.4"
    connection_timeout_s: float = 5.0
    deploy_timeout_s: float = 5.0
    download_timeout_s: float = 15.0
    download_retries: int = 3
    _running: bool = field(default=False, init=False, repr=False)
    _frame_handler: FrameHandler | None = field(default=None, init=False, repr=False)
    _error_handler: ErrorHandler | None = field(default=None, init=False, repr=False)
    _server_stream: object | None = field(default=None, init=False, repr=False)
    _video_socket: socket.socket | None = field(default=None, init=False, repr=False)
    _control_socket: socket.socket | None = field(default=None, init=False, repr=False)
    _stream_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _width: int = field(default=0, init=False, repr=False)
    _height: int = field(default=0, init=False, repr=False)
    _codec_name: str = field(default="h264", init=False, repr=False)
    _device: Any | None = field(default=None, init=False, repr=False)

    def start(self, frame_handler: FrameHandler, error_handler: ErrorHandler) -> None:
        if self._running:
            raise RuntimeError("scrcpy proto backend already started")
        self._frame_handler = frame_handler
        self._error_handler = error_handler
        self._running = True
        try:
            devices = adb.device_list()
            if not devices:
                raise DeviceNotFoundError("no ADB device found; check `adb devices` and USB debugging")
            self._device = adb.device(serial=self.device_serial) if self.device_serial else devices[0]
            server_jar = self._ensure_server_jar()
            self._deploy_server(server_jar)
            self._init_server_connection()
            self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
            self._stream_thread.start()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        self._running = False
        if self._stream_thread is not None:
            self._stream_thread.join(timeout=1.0)
            self._stream_thread = None
        self._cleanup_connections()

    def _ensure_server_jar(self) -> Path:
        cache_dir = Path.home() / ".cache" / "android-stream"
        cache_dir.mkdir(parents=True, exist_ok=True)
        server_path = cache_dir / f"scrcpy-server-v{self.scrcpy_version}.jar"
        expected_sha = SCRCPY_SERVER_SHA256.get(self.scrcpy_version)
        if expected_sha is None:
            raise ServerDownloadError(
                f"unsupported scrcpy server version for integrity check: {self.scrcpy_version}. "
                "please add its sha256 to SCRCPY_SERVER_SHA256."
            )

        if server_path.exists() and server_path.stat().st_size > 0:
            if self._sha256_file(server_path) == expected_sha:
                return server_path
            server_path.unlink(missing_ok=True)

        url = (
            "https://github.com/Genymobile/scrcpy/releases/download/"
            f"v{self.scrcpy_version}/scrcpy-server-v{self.scrcpy_version}"
        )
        self._download_with_retry(url, server_path)
        actual_sha = self._sha256_file(server_path)
        if actual_sha != expected_sha:
            server_path.unlink(missing_ok=True)
            raise ChecksumMismatchError(
                "downloaded scrcpy server checksum mismatch; file removed for safety."
            )
        return server_path

    def _deploy_server(self, server_jar: Path) -> None:
        self._device.sync.push(str(server_jar), "/data/local/tmp/scrcpy-server.jar")
        commands = [
            "CLASSPATH=/data/local/tmp/scrcpy-server.jar",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            self.scrcpy_version,
            "log_level=info",
            f"max_size={self.max_size}",
            f"max_fps={self.max_fps}",
            f"video_bit_rate={self.bitrate}",
            "video_codec=h264",
            "tunnel_forward=true",
            "send_frame_meta=false",
            "control=true",
            "audio=false",
            "show_touches=false",
            "stay_awake=false",
            "power_off_on_close=false",
            "clipboard_autosync=false",
        ]
        self._server_stream = self._device.shell(commands, stream=True)
        # 等待 server 拉起并创建 localabstract:scrcpy，避免在异常设备上无限阻塞。
        self._read_server_boot_log_with_timeout()

    def _init_server_connection(self) -> None:
        deadline = time.time() + self.connection_timeout_s
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                self._video_socket = self._device.create_connection(Network.LOCAL_ABSTRACT, "scrcpy")
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.1)
        else:
            raise HandshakeError(f"failed to connect scrcpy video socket: {last_error}")

        self._control_socket = self._device.create_connection(Network.LOCAL_ABSTRACT, "scrcpy")
        dummy_byte = self._recv_exact(self._video_socket, 1)
        if dummy_byte != b"\x00":
            raise HandshakeError("invalid scrcpy handshake dummy byte")

        # device name (64 bytes)
        _ = self._recv_exact(self._video_socket, 64)
        codec_name = self._recv_exact(self._video_socket, 4).decode("ascii", errors="ignore").strip("\x00")
        if codec_name not in {"h264", "h265", "av1"}:
            raise HandshakeError(f"unexpected scrcpy codec tag: {codec_name!r}")
        self._codec_name = codec_name

        # scrcpy v2.x sends initial video size as two big-endian uint32.
        res = self._recv_exact(self._video_socket, 8)
        self._width, self._height = struct.unpack(">II", res)
        if self._width <= 0 or self._height <= 0:
            raise HandshakeError("invalid device resolution from scrcpy server")
        self._video_socket.setblocking(False)

    def _stream_loop(self) -> None:
        if self._video_socket is None:
            return
        codec = CodecContext.create(self._codec_name, "r")
        try:
            while self._running:
                try:
                    raw = self._video_socket.recv(0x10000)
                    if not raw:
                        raise StreamDisconnectedError("scrcpy video socket disconnected")
                    packets = codec.parse(raw)
                    for packet in packets:
                        frames = codec.decode(packet)
                        for frame in frames:
                            arr = frame.to_ndarray(format="bgr24")
                            frame_packet = FramePacket.from_bgr(arr)
                            if self._frame_handler is not None:
                                self._frame_handler(frame_packet)
                except BlockingIOError:
                    time.sleep(0.005)
                except InvalidDataError:
                    # 避免个别损坏 NAL 影响整条流，继续消费后续数据。
                    continue
        except Exception as exc:
            self._running = False
            self._cleanup_connections()
            if isinstance(exc, (DeviceNotFoundError, HandshakeError, StreamDisconnectedError)):
                self._notify_error(exc)
            else:
                self._notify_error(DecodeError(str(exc)))

    def _notify_error(self, exc: Exception) -> None:
        if self._error_handler is not None:
            self._error_handler(exc)

    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise HandshakeError("socket closed during handshake")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _download_with_retry(self, url: str, target: Path) -> None:
        last_error: Exception | None = None
        for attempt in range(1, self.download_retries + 1):
            try:
                with urllib.request.urlopen(url, timeout=self.download_timeout_s) as resp, target.open("wb") as f:
                    shutil.copyfileobj(resp, f)
                return
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                target.unlink(missing_ok=True)
                if attempt < self.download_retries:
                    time.sleep(0.3 * attempt)
        raise ServerDownloadError(f"failed to download scrcpy server from {url}: {last_error}")

    def _cleanup_connections(self) -> None:
        for sock in (self._video_socket, self._control_socket):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        self._video_socket = None
        self._control_socket = None
        if self._server_stream is not None:
            try:
                self._server_stream.close()
            except Exception:
                pass
            self._server_stream = None

    def _read_server_boot_log_with_timeout(self) -> bytes:
        if self._server_stream is None:
            raise HandshakeError("server stream is not initialized")

        result: dict[str, bytes | Exception] = {}

        def _reader() -> None:
            try:
                result["data"] = self._server_stream.read(10)
            except Exception as exc:  # pragma: no cover - depends on adb stream internals
                result["error"] = exc

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        thread.join(timeout=self.deploy_timeout_s)
        if thread.is_alive():
            raise HandshakeError(
                f"scrcpy server startup log read timeout after {self.deploy_timeout_s}s"
            )
        if "error" in result:
            raise HandshakeError(f"failed reading scrcpy server startup log: {result['error']}")
        data = result.get("data", b"")
        if not isinstance(data, bytes):
            raise HandshakeError("invalid scrcpy server startup log payload")
        return data
