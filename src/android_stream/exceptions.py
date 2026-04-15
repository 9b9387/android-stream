from __future__ import annotations


class AndroidStreamError(Exception):
    """Base exception for android-stream SDK."""


class StreamStartError(AndroidStreamError):
    """Raised when stream startup fails."""


class DeviceNotFoundError(StreamStartError):
    """Raised when no ADB device is available."""


class ServerDownloadError(StreamStartError):
    """Raised when scrcpy server download fails."""


class ChecksumMismatchError(StreamStartError):
    """Raised when downloaded server checksum does not match expected hash."""


class HandshakeError(StreamStartError):
    """Raised when scrcpy protocol handshake fails."""


class StreamDisconnectedError(AndroidStreamError):
    """Raised when stream socket is disconnected unexpectedly."""


class DecodeError(AndroidStreamError):
    """Raised when decoder fails to parse stream frames."""
