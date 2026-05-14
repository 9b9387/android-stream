"""Pure-Python helpers for the scrcpy v4.0 wire protocol.

Only what we need for a browser bridge:

- parse the ``codec id`` (4 bytes) preamble on the video / audio sockets
- parse the 12-byte session packet (video only) and the 12-byte frame header
- serialize :class:`ControlMessage` instances back into bytes the scrcpy
  server understands

The byte layouts mirror exactly what the official Java server reads/writes.
The ground truth lives in ``scrcpy/server/src/main/java/com/genymobile/scrcpy/``
- :class:`Streamer.writeFrameMeta` (video / audio packet header)
- :class:`Streamer.writeSessionMeta` (video-only session header)
- :class:`ControlMessageReader` (control message deserializer)
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass
from typing import Final


# ---------------------------------------------------------------------------
# Stream meta constants
# ---------------------------------------------------------------------------

# Top three bits of the 8-byte ``ptsAndFlags`` header.
SESSION_PACKET_FLAG: Final[int] = 1 << 63
CONFIG_PACKET_FLAG: Final[int] = 1 << 62
KEY_FRAME_FLAG: Final[int] = 1 << 61
PTS_MASK: Final[int] = (1 << 61) - 1


class VideoCodec(str, enum.Enum):
    H264 = "h264"
    H265 = "h265"
    AV1 = "av1"


class AudioCodec(str, enum.Enum):
    OPUS = "opus"
    AAC = "aac"
    FLAC = "flac"
    RAW = "raw"


def _codec_id(name: str) -> int:
    raw = name.encode("ascii")
    if len(raw) > 4:
        raise ValueError(f"codec name too long: {name!r}")
    raw = raw.rjust(4, b"\x00")
    return int.from_bytes(raw, "big")


VIDEO_CODEC_IDS: Final[dict[int, VideoCodec]] = {
    _codec_id(c.value): c for c in VideoCodec
}
AUDIO_CODEC_IDS: Final[dict[int, AudioCodec]] = {
    _codec_id(c.value): c for c in AudioCodec
}


# ---------------------------------------------------------------------------
# Frame headers
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SessionPacket:
    width: int
    height: int
    client_resized: bool


@dataclass(slots=True, frozen=True)
class FrameMeta:
    pts_us: int
    size: int
    config: bool
    key_frame: bool


def parse_frame_header(header: bytes) -> SessionPacket | FrameMeta:
    """Parse one 12-byte stream header from the video/audio socket.

    The video stream may interleave *session* packets (signalled by the MSB
    of the first byte) and *media* packets. Audio streams contain only media
    packets.
    """
    if len(header) != 12:
        raise ValueError(f"frame header must be 12 bytes, got {len(header)}")

    pts_and_flags, size = struct.unpack(">QI", header)
    if pts_and_flags & SESSION_PACKET_FLAG:
        # Session packet: 4-byte flags + 4-byte width + 4-byte height. The
        # ``size`` field of the standard header doubles as the video height.
        flags_high = (pts_and_flags >> 32) & 0xFFFFFFFF
        width = pts_and_flags & 0xFFFFFFFF
        height = size
        return SessionPacket(
            width=width,
            height=height,
            client_resized=bool(flags_high & 0x1),
        )

    return FrameMeta(
        pts_us=pts_and_flags & PTS_MASK,
        size=size,
        config=bool(pts_and_flags & CONFIG_PACKET_FLAG),
        key_frame=bool(pts_and_flags & KEY_FRAME_FLAG),
    )


# ---------------------------------------------------------------------------
# Control messages (client -> device)
# ---------------------------------------------------------------------------


class CONTROL_MSG_TYPE(enum.IntEnum):
    INJECT_KEYCODE = 0
    INJECT_TEXT = 1
    INJECT_TOUCH_EVENT = 2
    INJECT_SCROLL_EVENT = 3
    BACK_OR_SCREEN_ON = 4
    EXPAND_NOTIFICATION_PANEL = 5
    EXPAND_SETTINGS_PANEL = 6
    COLLAPSE_PANELS = 7
    GET_CLIPBOARD = 8
    SET_CLIPBOARD = 9
    SET_DISPLAY_POWER = 10
    ROTATE_DEVICE = 11
    UHID_CREATE = 12
    UHID_INPUT = 13
    UHID_DESTROY = 14
    OPEN_HARD_KEYBOARD_SETTINGS = 15
    START_APP = 16
    RESET_VIDEO = 17
    CAMERA_SET_TORCH = 18
    CAMERA_ZOOM_IN = 19
    CAMERA_ZOOM_OUT = 20
    RESIZE_DISPLAY = 21


# Same constants as android.view.MotionEvent.ACTION_*.
ACTION_DOWN: Final[int] = 0
ACTION_UP: Final[int] = 1
ACTION_MOVE: Final[int] = 2

# android.view.KeyEvent.ACTION_*.
KEY_ACTION_DOWN: Final[int] = 0
KEY_ACTION_UP: Final[int] = 1

# android.view.MotionEvent.BUTTON_*.
BUTTON_PRIMARY: Final[int] = 1 << 0
BUTTON_SECONDARY: Final[int] = 1 << 1
BUTTON_TERTIARY: Final[int] = 1 << 2

# Well-known pointer ids used by scrcpy. Anything else can be used for
# secondary fingers.
POINTER_ID_MOUSE: Final[int] = 0xFFFF_FFFF_FFFF_FFFF
POINTER_ID_GENERIC_FINGER: Final[int] = 0xFFFF_FFFF_FFFF_FFFE

INJECT_TEXT_MAX_LENGTH: Final[int] = 300
SET_CLIPBOARD_TEXT_MAX_LENGTH: Final[int] = (1 << 18) - 14


def _u16_fixed_point(value: float) -> int:
    """Convert ``[0.0, 1.0]`` to the wire 16-bit fixed-point format."""
    if value >= 1.0:
        return 0xFFFF
    if value <= 0.0:
        return 0
    return int(value * 0x1_0000) & 0xFFFF


def _i16_fixed_point(value: float) -> int:
    """Convert ``[-1.0, 1.0]`` to the wire 16-bit fixed-point format."""
    if value >= 1.0:
        return 0x7FFF
    if value <= -1.0:
        return -0x8000 & 0xFFFF
    return int(value * 0x8000) & 0xFFFF


@dataclass(slots=True)
class ControlMessage:
    """Lightweight equivalent of the Java ``ControlMessage``.

    Construct via the ``create_*`` class methods, then call
    :func:`serialize_control_message` to obtain the wire bytes.
    """

    type: CONTROL_MSG_TYPE
    # All other fields are optional and message-type specific.
    action: int = 0
    keycode: int = 0
    repeat: int = 0
    meta_state: int = 0
    pointer_id: int = 0
    pressure: float = 1.0
    action_button: int = 0
    buttons: int = 0
    x: int = 0
    y: int = 0
    screen_width: int = 0
    screen_height: int = 0
    h_scroll: float = 0.0
    v_scroll: float = 0.0
    text: str = ""
    paste: bool = False
    sequence: int = 0
    copy_key: int = 0
    on: bool = False
    width: int = 0
    height: int = 0

    # ----- factories ------------------------------------------------------
    @classmethod
    def inject_keycode(
        cls,
        action: int,
        keycode: int,
        repeat: int = 0,
        meta_state: int = 0,
    ) -> "ControlMessage":
        return cls(
            type=CONTROL_MSG_TYPE.INJECT_KEYCODE,
            action=action,
            keycode=keycode,
            repeat=repeat,
            meta_state=meta_state,
        )

    @classmethod
    def inject_text(cls, text: str) -> "ControlMessage":
        return cls(type=CONTROL_MSG_TYPE.INJECT_TEXT, text=text)

    @classmethod
    def inject_touch_event(
        cls,
        *,
        action: int,
        pointer_id: int,
        x: int,
        y: int,
        screen_width: int,
        screen_height: int,
        pressure: float = 1.0,
        action_button: int = 0,
        buttons: int = 0,
    ) -> "ControlMessage":
        return cls(
            type=CONTROL_MSG_TYPE.INJECT_TOUCH_EVENT,
            action=action,
            pointer_id=pointer_id,
            x=x,
            y=y,
            screen_width=screen_width,
            screen_height=screen_height,
            pressure=pressure,
            action_button=action_button,
            buttons=buttons,
        )

    @classmethod
    def inject_scroll_event(
        cls,
        *,
        x: int,
        y: int,
        screen_width: int,
        screen_height: int,
        h_scroll: float,
        v_scroll: float,
        buttons: int = 0,
    ) -> "ControlMessage":
        return cls(
            type=CONTROL_MSG_TYPE.INJECT_SCROLL_EVENT,
            x=x,
            y=y,
            screen_width=screen_width,
            screen_height=screen_height,
            h_scroll=h_scroll,
            v_scroll=v_scroll,
            buttons=buttons,
        )

    @classmethod
    def back_or_screen_on(cls, action: int) -> "ControlMessage":
        return cls(type=CONTROL_MSG_TYPE.BACK_OR_SCREEN_ON, action=action)

    @classmethod
    def empty(cls, msg_type: CONTROL_MSG_TYPE) -> "ControlMessage":
        return cls(type=msg_type)

    @classmethod
    def set_clipboard(
        cls, *, sequence: int, text: str, paste: bool
    ) -> "ControlMessage":
        return cls(
            type=CONTROL_MSG_TYPE.SET_CLIPBOARD,
            sequence=sequence,
            text=text,
            paste=paste,
        )

    @classmethod
    def set_display_power(cls, on: bool) -> "ControlMessage":
        return cls(type=CONTROL_MSG_TYPE.SET_DISPLAY_POWER, on=on)


def _truncate_utf8(text: str, max_bytes: int) -> bytes:
    """Truncate ``text`` so its UTF-8 encoding fits in ``max_bytes`` bytes.

    The Java server (``StringUtils.getUtf8TruncationIndex``) enforces this
    same limit and silently truncates beyond it. We match that behavior so
    the bytes we put on the wire are always valid UTF-8 even when the input
    is too long. The trailing partial character (if any) is dropped — never
    half a multi-byte sequence.
    """
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return raw
    truncated = bytearray(raw[:max_bytes])
    # Walk back over any continuation bytes (top bits ``10``) and the leading
    # byte that introduced the partial sequence (top bits ``11``). Pure ASCII
    # boundaries (top bit clear) are always safe.
    while truncated:
        last = truncated[-1]
        if last < 0x80:
            break
        if (last & 0xC0) == 0x80:
            truncated.pop()
            continue
        # Leading byte of an incomplete multi-byte sequence — drop it too.
        truncated.pop()
        break
    return bytes(truncated)


def serialize_control_message(msg: ControlMessage) -> bytes:
    """Serialize ``msg`` into the binary form expected by the device server."""
    t = msg.type
    if t == CONTROL_MSG_TYPE.INJECT_KEYCODE:
        return struct.pack(
            ">BBIII", t, msg.action & 0xFF, msg.keycode, msg.repeat, msg.meta_state
        )
    if t == CONTROL_MSG_TYPE.INJECT_TEXT:
        raw = _truncate_utf8(msg.text, INJECT_TEXT_MAX_LENGTH)
        return struct.pack(">BI", t, len(raw)) + raw
    if t == CONTROL_MSG_TYPE.INJECT_TOUCH_EVENT:
        # Position payload: x(i32 BE) y(i32 BE) sw(u16 BE) sh(u16 BE).
        position = struct.pack(
            ">iiHH",
            int(msg.x),
            int(msg.y),
            msg.screen_width & 0xFFFF,
            msg.screen_height & 0xFFFF,
        )
        return (
            struct.pack(">BBQ", t, msg.action & 0xFF, msg.pointer_id & 0xFFFFFFFFFFFFFFFF)
            + position
            + struct.pack(
                ">HII",
                _u16_fixed_point(msg.pressure),
                msg.action_button & 0xFFFFFFFF,
                msg.buttons & 0xFFFFFFFF,
            )
        )
    if t == CONTROL_MSG_TYPE.INJECT_SCROLL_EVENT:
        position = struct.pack(
            ">iiHH",
            int(msg.x),
            int(msg.y),
            msg.screen_width & 0xFFFF,
            msg.screen_height & 0xFFFF,
        )
        # Wire range is [-1, 1], mapped from a [-16, 16] application-level
        # range. We mirror the C client's CLAMP and division by 16.
        h = max(-1.0, min(1.0, msg.h_scroll / 16.0))
        v = max(-1.0, min(1.0, msg.v_scroll / 16.0))
        return (
            struct.pack(">B", t)
            + position
            + struct.pack(
                ">HHI",
                _i16_fixed_point(h),
                _i16_fixed_point(v),
                msg.buttons & 0xFFFFFFFF,
            )
        )
    if t == CONTROL_MSG_TYPE.BACK_OR_SCREEN_ON:
        return struct.pack(">BB", t, msg.action & 0xFF)
    if t == CONTROL_MSG_TYPE.GET_CLIPBOARD:
        return struct.pack(">BB", t, msg.copy_key & 0xFF)
    if t == CONTROL_MSG_TYPE.SET_CLIPBOARD:
        raw = _truncate_utf8(msg.text, SET_CLIPBOARD_TEXT_MAX_LENGTH)
        return (
            struct.pack(">BQB", t, msg.sequence & 0xFFFFFFFFFFFFFFFF, 1 if msg.paste else 0)
            + struct.pack(">I", len(raw))
            + raw
        )
    if t == CONTROL_MSG_TYPE.SET_DISPLAY_POWER:
        return struct.pack(">BB", t, 1 if msg.on else 0)
    if t == CONTROL_MSG_TYPE.CAMERA_SET_TORCH:
        return struct.pack(">BB", t, 1 if msg.on else 0)
    if t == CONTROL_MSG_TYPE.RESIZE_DISPLAY:
        return struct.pack(
            ">BHH", t, msg.width & 0xFFFF, msg.height & 0xFFFF
        )
    if t in (
        CONTROL_MSG_TYPE.EXPAND_NOTIFICATION_PANEL,
        CONTROL_MSG_TYPE.EXPAND_SETTINGS_PANEL,
        CONTROL_MSG_TYPE.COLLAPSE_PANELS,
        CONTROL_MSG_TYPE.ROTATE_DEVICE,
        CONTROL_MSG_TYPE.OPEN_HARD_KEYBOARD_SETTINGS,
        CONTROL_MSG_TYPE.RESET_VIDEO,
        CONTROL_MSG_TYPE.CAMERA_ZOOM_IN,
        CONTROL_MSG_TYPE.CAMERA_ZOOM_OUT,
    ):
        return struct.pack(">B", t)

    raise ValueError(f"unsupported control message type: {t}")


# ---------------------------------------------------------------------------
# Device messages (device -> client)
# ---------------------------------------------------------------------------


class DEVICE_MSG_TYPE(enum.IntEnum):
    CLIPBOARD = 0
    ACK_CLIPBOARD = 1
    UHID_OUTPUT = 2


@dataclass(slots=True, frozen=True)
class DeviceMessage:
    type: DEVICE_MSG_TYPE
    text: str | None = None
    sequence: int | None = None
    uhid_id: int | None = None
    data: bytes | None = None


def parse_device_message(read_exact) -> DeviceMessage:
    """Parse one device message using a blocking ``read_exact(n) -> bytes``.

    The reader callable is expected to either return exactly ``n`` bytes or
    raise an :class:`OSError`-like exception on disconnect.
    """
    head = read_exact(1)
    msg_type = DEVICE_MSG_TYPE(head[0])
    if msg_type == DEVICE_MSG_TYPE.CLIPBOARD:
        (length,) = struct.unpack(">I", read_exact(4))
        text = read_exact(length).decode("utf-8", errors="replace")
        return DeviceMessage(type=msg_type, text=text)
    if msg_type == DEVICE_MSG_TYPE.ACK_CLIPBOARD:
        (sequence,) = struct.unpack(">Q", read_exact(8))
        return DeviceMessage(type=msg_type, sequence=sequence)
    if msg_type == DEVICE_MSG_TYPE.UHID_OUTPUT:
        uhid_id, length = struct.unpack(">HH", read_exact(4))
        return DeviceMessage(
            type=msg_type, uhid_id=uhid_id, data=read_exact(length)
        )
    raise ValueError(f"unknown device message type: {msg_type}")
