"""Connect to a scrcpy v4.0 server and pump packets without decoding them.

The browser is responsible for decoding via WebCodecs / MSE, so this backend
intentionally avoids any video/audio decode work in Python: it parses the
wire framing only, then forwards opaque encoded payloads (H.264 / H.265 / AV1
NAL units, OPUS / AAC / FLAC packets) through callbacks.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import socket
import struct
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Final
from urllib.error import URLError

from adbutils import Network, adb

from android_stream.exceptions import (
    ChecksumMismatchError,
    DeviceNotFoundError,
    HandshakeError,
    ServerDownloadError,
    StreamDisconnectedError,
)

from .protocol import (
    AUDIO_CODEC_IDS,
    VIDEO_CODEC_IDS,
    AudioCodec,
    ControlMessage,
    DeviceMessage,
    FrameMeta,
    SessionPacket,
    VideoCodec,
    parse_device_message,
    parse_frame_header,
    serialize_control_message,
)

log = logging.getLogger(__name__)


SCRCPY_V4_VERSION: Final[str] = "4.0"
SCRCPY_V4_SERVER_SHA256: Final[str] = (
    "84924bd564a1eb6089c872c7521f968058977f91f5ff02514a8c74aff3210f3a"
)
SCRCPY_V4_SERVER_URL: Final[str] = (
    "https://github.com/Genymobile/scrcpy/releases/download/"
    f"v{SCRCPY_V4_VERSION}/scrcpy-server-v{SCRCPY_V4_VERSION}"
)


VideoPacketHandler = Callable[[FrameMeta, bytes], None]
AudioPacketHandler = Callable[[FrameMeta, bytes], None]
SessionHandler = Callable[[SessionPacket], None]
DeviceMessageHandler = Callable[[DeviceMessage], None]
ErrorHandler = Callable[[Exception], None]


@dataclass(slots=True, frozen=True)
class StreamMeta:
    """Initial metadata learned during the scrcpy handshake."""

    device_name: str
    video_codec: VideoCodec | None
    audio_codec: AudioCodec | None
    width: int
    height: int


@dataclass(slots=True)
class ScrcpyV4Backend:
    """A single connection to one scrcpy v4.0 server instance."""

    device_serial: str | None = None
    max_size: int = 0
    max_fps: int = 30
    video_bit_rate: int = 8_000_000
    audio_bit_rate: int = 128_000
    video: bool = True
    audio: bool = True
    control: bool = True
    video_codec: VideoCodec = VideoCodec.H264
    audio_codec: AudioCodec = AudioCodec.OPUS
    socket_name: str = "scrcpy"
    connection_timeout_s: float = 8.0
    deploy_timeout_s: float = 5.0
    download_timeout_s: float = 30.0
    download_retries: int = 3
    server_jar_path: Path | None = None

    # ----- mutable state (not config) -------------------------------------
    _running: bool = field(default=False, init=False, repr=False)
    _video_handler: VideoPacketHandler | None = field(default=None, init=False, repr=False)
    _audio_handler: AudioPacketHandler | None = field(default=None, init=False, repr=False)
    _session_handler: SessionHandler | None = field(default=None, init=False, repr=False)
    _device_message_handler: DeviceMessageHandler | None = field(default=None, init=False, repr=False)
    _error_handler: ErrorHandler | None = field(default=None, init=False, repr=False)
    _meta: StreamMeta | None = field(default=None, init=False, repr=False)
    _device: Any = field(default=None, init=False, repr=False)
    _server_stream: Any = field(default=None, init=False, repr=False)
    _video_socket: socket.socket | None = field(default=None, init=False, repr=False)
    _audio_socket: socket.socket | None = field(default=None, init=False, repr=False)
    _control_socket: socket.socket | None = field(default=None, init=False, repr=False)
    _control_send_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _video_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _audio_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _control_recv_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _log_drainer_thread: threading.Thread | None = field(default=None, init=False, repr=False)

    @property
    def meta(self) -> StreamMeta | None:
        return self._meta

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ start
    def start(
        self,
        *,
        on_video: VideoPacketHandler | None = None,
        on_audio: AudioPacketHandler | None = None,
        on_session: SessionHandler | None = None,
        on_device_message: DeviceMessageHandler | None = None,
        on_error: ErrorHandler | None = None,
    ) -> StreamMeta:
        if self._running:
            raise RuntimeError("scrcpy v4 backend already running")
        self._video_handler = on_video
        self._audio_handler = on_audio
        self._session_handler = on_session
        self._device_message_handler = on_device_message
        self._error_handler = on_error
        try:
            devices = adb.device_list()
            if not devices:
                raise DeviceNotFoundError(
                    "no ADB device found; check `adb devices` and USB debugging"
                )
            self._device = (
                adb.device(serial=self.device_serial)
                if self.device_serial
                else devices[0]
            )
            jar = self.server_jar_path or _ensure_server_jar(
                download_timeout_s=self.download_timeout_s,
                retries=self.download_retries,
            )
            self._spawn_server(jar)
            self._connect_sockets()
            self._meta = self._handshake()
            self._running = True
            self._spawn_workers()
            return self._meta
        except Exception:
            self.stop()
            raise

    # ------------------------------------------------------------------ stop
    def stop(self) -> None:
        self._running = False
        for thread in (self._video_thread, self._audio_thread, self._control_recv_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)
        self._video_thread = None
        self._audio_thread = None
        self._control_recv_thread = None
        self._cleanup_connections()

    # ------------------------------------------------------------- control I/O
    def send_control_message(self, msg: ControlMessage) -> None:
        if not self._running or self._control_socket is None:
            raise RuntimeError("control channel is not available")
        payload = serialize_control_message(msg)
        with self._control_send_lock:
            self._control_socket.sendall(payload)

    # ------------------------------------------------------------- internals
    def _spawn_server(self, jar: Path) -> None:
        device = self._device
        device.sync.push(str(jar), "/data/local/tmp/scrcpy-server.jar")
        commands = [
            "CLASSPATH=/data/local/tmp/scrcpy-server.jar",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            SCRCPY_V4_VERSION,
            "log_level=info",
            "tunnel_forward=true",
            f"video={'true' if self.video else 'false'}",
            f"audio={'true' if self.audio else 'false'}",
            f"control={'true' if self.control else 'false'}",
            f"video_codec={self.video_codec.value}",
            f"audio_codec={self.audio_codec.value}",
            f"max_size={self.max_size}",
            f"max_fps={self.max_fps}",
            f"video_bit_rate={self.video_bit_rate}",
            f"audio_bit_rate={self.audio_bit_rate}",
            "send_device_meta=true",
            "send_frame_meta=true",
            "send_dummy_byte=true",
            "send_stream_meta=true",
            "cleanup=false",
            "stay_awake=false",
            "show_touches=false",
            "power_off_on_close=false",
            "clipboard_autosync=false",
        ]
        self._server_stream = device.shell(commands, stream=True)
        # The server prints a startup line before binding the local socket;
        # consume it so we don't deadlock if the server fails to launch.
        startup_log = _read_first_chunk_with_timeout(
            self._server_stream, timeout_s=self.deploy_timeout_s
        )
        if startup_log:
            try:
                log.debug("scrcpy server stdout: %s", startup_log.decode("utf-8", "replace").strip())
            except Exception:
                pass
        if self._log_drainer_thread is None:
            self._log_drainer_thread = threading.Thread(
                target=self._drain_server_log,
                name="scrcpy-v4-srvlog",
                daemon=True,
            )
            self._log_drainer_thread.start()

    def _connect_sockets(self) -> None:
        deadline = time.time() + self.connection_timeout_s
        last_error: Exception | None = None
        order: list[str] = []
        if self.video:
            order.append("video")
        if self.audio:
            order.append("audio")
        if self.control:
            order.append("control")
        if not order:
            raise HandshakeError("at least one of video/audio/control must be enabled")

        sockets: dict[str, socket.socket] = {}
        for name in order:
            while time.time() < deadline:
                try:
                    sock = self._device.create_connection(
                        Network.LOCAL_ABSTRACT, self.socket_name
                    )
                    sockets[name] = sock
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.1)
            else:
                for s in sockets.values():
                    try:
                        s.close()
                    except Exception:
                        pass
                raise HandshakeError(
                    f"failed to connect scrcpy {name} socket: {last_error}"
                )

        self._video_socket = sockets.get("video")
        self._audio_socket = sockets.get("audio")
        self._control_socket = sockets.get("control")

    def _handshake(self) -> StreamMeta:
        # The first opened socket carries the dummy byte (when tunnel_forward
        # is set) and the optional 64-byte device name.
        first = self._first_socket()
        if first is None:
            raise HandshakeError("no socket opened during handshake")

        first.settimeout(self.connection_timeout_s)
        dummy = _recv_exact(first, 1)
        if dummy != b"\x00":
            raise HandshakeError(f"unexpected dummy byte: {dummy!r}")
        device_name_raw = _recv_exact(first, 64)
        device_name = device_name_raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")

        video_codec: VideoCodec | None = None
        audio_codec: AudioCodec | None = None
        width = 0
        height = 0

        if self._video_socket is not None:
            self._video_socket.settimeout(self.connection_timeout_s)
            (codec_id,) = struct.unpack(">I", _recv_exact(self._video_socket, 4))
            video_codec = VIDEO_CODEC_IDS.get(codec_id)
            if video_codec is None:
                raise HandshakeError(f"unsupported video codec id: {codec_id:#010x}")
            session_header = _recv_exact(self._video_socket, 12)
            session = parse_frame_header(session_header)
            if not isinstance(session, SessionPacket):
                raise HandshakeError(
                    "expected video session packet immediately after codec id"
                )
            width = session.width
            height = session.height

        if self._audio_socket is not None:
            self._audio_socket.settimeout(self.connection_timeout_s)
            (codec_id,) = struct.unpack(">I", _recv_exact(self._audio_socket, 4))
            audio_codec = AUDIO_CODEC_IDS.get(codec_id)
            if audio_codec is None and codec_id == 0:
                # Server explicitly disabled audio (e.g. unsupported on device).
                log.info("scrcpy server disabled audio capture; continuing video-only")
                audio_codec = None
                try:
                    self._audio_socket.close()
                except Exception:
                    pass
                self._audio_socket = None
            elif audio_codec is None and codec_id == 1:
                raise HandshakeError(
                    "scrcpy server reported a fatal audio configuration error"
                )
            elif audio_codec is None:
                raise HandshakeError(f"unsupported audio codec id: {codec_id:#010x}")

        # Switch back to blocking mode (no timeout) for the streaming loops.
        for sock in (self._video_socket, self._audio_socket, self._control_socket):
            if sock is not None:
                sock.settimeout(None)

        return StreamMeta(
            device_name=device_name,
            video_codec=video_codec,
            audio_codec=audio_codec,
            width=width,
            height=height,
        )

    def _first_socket(self) -> socket.socket | None:
        if self._video_socket is not None:
            return self._video_socket
        if self._audio_socket is not None:
            return self._audio_socket
        return self._control_socket

    def _spawn_workers(self) -> None:
        if self._video_socket is not None:
            self._video_thread = threading.Thread(
                target=self._video_loop, name="scrcpy-v4-video", daemon=True
            )
            self._video_thread.start()
        if self._audio_socket is not None:
            self._audio_thread = threading.Thread(
                target=self._audio_loop, name="scrcpy-v4-audio", daemon=True
            )
            self._audio_thread.start()
        if self._control_socket is not None:
            self._control_recv_thread = threading.Thread(
                target=self._control_recv_loop,
                name="scrcpy-v4-control-recv",
                daemon=True,
            )
            self._control_recv_thread.start()

    def _video_loop(self) -> None:
        sock = self._video_socket
        if sock is None:
            return
        try:
            while self._running:
                header = _recv_exact_or_eof(sock, 12)
                if header is None:
                    raise StreamDisconnectedError("video socket closed by device")
                parsed = parse_frame_header(header)
                if isinstance(parsed, SessionPacket):
                    if self._session_handler is not None:
                        self._session_handler(parsed)
                    continue
                payload = _recv_exact(sock, parsed.size)
                if self._video_handler is not None:
                    self._video_handler(parsed, payload)
        except Exception as exc:
            self._fail(exc)

    def _audio_loop(self) -> None:
        sock = self._audio_socket
        if sock is None:
            return
        try:
            while self._running:
                header = _recv_exact_or_eof(sock, 12)
                if header is None:
                    raise StreamDisconnectedError("audio socket closed by device")
                parsed = parse_frame_header(header)
                if isinstance(parsed, SessionPacket):
                    # Per protocol, audio stream contains only media packets.
                    log.warning("ignoring unexpected session packet on audio stream")
                    continue
                payload = _recv_exact(sock, parsed.size)
                if self._audio_handler is not None:
                    self._audio_handler(parsed, payload)
        except Exception as exc:
            self._fail(exc)

    def _control_recv_loop(self) -> None:
        sock = self._control_socket
        if sock is None:
            return

        def reader(n: int) -> bytes:
            data = _recv_exact_or_eof(sock, n)
            if data is None:
                raise StreamDisconnectedError("control socket closed by device")
            return data

        try:
            while self._running:
                msg = parse_device_message(reader)
                if self._device_message_handler is not None:
                    self._device_message_handler(msg)
        except StreamDisconnectedError:
            return
        except Exception as exc:
            self._fail(exc)

    def _drain_server_log(self) -> None:
        # Continuously copy scrcpy-server stdout/stderr to our logger, mostly
        # so that `INFO`/`WARN`/`ERROR` lines from MediaCodec & friends show
        # up alongside our backend logs when troubleshooting.
        stream = self._server_stream
        if stream is None:
            return
        buffer = b""
        try:
            while self._running:
                chunk = stream.read(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if not text:
                        continue
                    if text.startswith("[server] WARN") or text.startswith("[server] ERROR"):
                        log.warning("scrcpy-server: %s", text)
                    else:
                        log.debug("scrcpy-server: %s", text)
        except Exception:
            return

    def _fail(self, exc: Exception) -> None:
        if not self._running:
            return
        self._running = False
        if self._error_handler is not None:
            try:
                self._error_handler(exc)
            except Exception:
                log.exception("scrcpy v4 error handler raised")
        # Don't tear down connections here: stop() is the canonical cleanup
        # and the supervising service is expected to call it.

    def _cleanup_connections(self) -> None:
        for sock in (self._video_socket, self._audio_socket, self._control_socket):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        self._video_socket = None
        self._audio_socket = None
        self._control_socket = None
        if self._server_stream is not None:
            try:
                self._server_stream.close()
            except Exception:
                pass
            self._server_stream = None


# ---------------------------------------------------------------------------
# Server jar management
# ---------------------------------------------------------------------------


def _ensure_server_jar(
    *,
    download_timeout_s: float,
    retries: int,
    cache_dir: Path | None = None,
) -> Path:
    base = cache_dir or Path.home() / ".cache" / "android-stream"
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"scrcpy-server-v{SCRCPY_V4_VERSION}.jar"
    if target.exists() and target.stat().st_size > 0:
        if _sha256(target) == SCRCPY_V4_SERVER_SHA256:
            return target
        target.unlink(missing_ok=True)

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(
                SCRCPY_V4_SERVER_URL, timeout=download_timeout_s
            ) as response, target.open("wb") as out:
                shutil.copyfileobj(response, out)
            break
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            target.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(0.5 * attempt)
    else:
        raise ServerDownloadError(
            f"failed to download scrcpy server from {SCRCPY_V4_SERVER_URL}: {last_error}"
        )

    actual = _sha256(target)
    if actual != SCRCPY_V4_SERVER_SHA256:
        target.unlink(missing_ok=True)
        raise ChecksumMismatchError(
            "downloaded scrcpy server checksum mismatch; file removed for safety."
        )
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_first_chunk_with_timeout(stream: Any, *, timeout_s: float) -> bytes:
    """Read up to a small number of bytes from a synchronous adb stream.

    The scrcpy server logs a startup banner before binding the local
    abstract socket. Reading it confirms the server actually started; if the
    read blocks forever we treat that as a failure (typical when a previous
    server instance is still holding the port).
    """
    result: dict[str, Any] = {}

    def _reader() -> None:
        try:
            result["data"] = stream.read(10)
        except Exception as exc:  # pragma: no cover - depends on adb stream
            result["error"] = exc

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        raise HandshakeError(
            f"scrcpy server startup log read timeout after {timeout_s}s"
        )
    if "error" in result:
        raise HandshakeError(f"failed reading scrcpy server startup log: {result['error']}")
    data = result.get("data") or b""
    if not isinstance(data, bytes):
        raise HandshakeError("invalid scrcpy server startup log payload")
    return data


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise StreamDisconnectedError("socket closed during read")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_exact_or_eof(sock: socket.socket, size: int) -> bytes | None:
    """Like :func:`_recv_exact` but returns ``None`` on a clean EOF."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        try:
            chunk = sock.recv(remaining)
        except OSError as exc:
            if not chunks:
                return None
            raise StreamDisconnectedError(f"socket read failed: {exc}") from exc
        if not chunk:
            if not chunks:
                return None
            raise StreamDisconnectedError("socket closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
