"""scrcpy v4.0 protocol bridge.

This package implements a Web-friendly bridge to the scrcpy server v4.0.

It only forwards encoded media packets (H.264 / Opus) and serialized control
messages, without decoding. The browser is expected to do hardware decoding via
WebCodecs (or similar).

Sub-modules:

- :mod:`protocol`: low-level frame meta parser, codec ids, control message
  serializer.
- :mod:`backend`: connect to scrcpy 4.0 server through ADB and pump packets.
- :mod:`service`: process-level lifecycle management with subscriber dispatch.
- :mod:`web`: FastAPI router exposing the bridge to a browser via WebSocket.
"""

from .protocol import (
    AUDIO_CODEC_IDS,
    CONFIG_PACKET_FLAG,
    CONTROL_MSG_TYPE,
    KEY_FRAME_FLAG,
    SESSION_PACKET_FLAG,
    VIDEO_CODEC_IDS,
    AudioCodec,
    ControlMessage,
    FrameMeta,
    SessionPacket,
    VideoCodec,
    parse_frame_header,
    serialize_control_message,
)
from .service import (
    MediaSubscription,
    ScrcpyV4Config,
    ScrcpyV4Service,
)

__all__ = [
    "AUDIO_CODEC_IDS",
    "AudioCodec",
    "CONFIG_PACKET_FLAG",
    "CONTROL_MSG_TYPE",
    "ControlMessage",
    "FrameMeta",
    "KEY_FRAME_FLAG",
    "MediaSubscription",
    "ScrcpyV4Config",
    "ScrcpyV4Service",
    "SESSION_PACKET_FLAG",
    "SessionPacket",
    "VIDEO_CODEC_IDS",
    "VideoCodec",
    "parse_frame_header",
    "serialize_control_message",
]
