from __future__ import annotations

import struct

import pytest

from android_stream.scrcpy_v4 import (
    AUDIO_CODEC_IDS,
    CONFIG_PACKET_FLAG,
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
from android_stream.scrcpy_v4.protocol import (
    ACTION_DOWN,
    BUTTON_PRIMARY,
    CONTROL_MSG_TYPE,
    POINTER_ID_MOUSE,
    DEVICE_MSG_TYPE,
    parse_device_message,
)


class TestCodecIdRegistry:
    def test_video_codec_ids_include_all_known_codecs(self) -> None:
        # Reading the codec id from the wire is what triggers our handshake
        # path, so the registry must round-trip every supported value. A
        # missing entry would surface as "unsupported video codec id" in
        # production, masking the real failure.
        assert set(VIDEO_CODEC_IDS.values()) == set(VideoCodec)

    def test_audio_codec_ids_include_all_known_codecs(self) -> None:
        assert set(AUDIO_CODEC_IDS.values()) == set(AudioCodec)

    def test_h264_codec_id_matches_ascii_value(self) -> None:
        # 0x68323634 == b"h264", per scrcpy/doc/develop.md.
        assert int.from_bytes(b"h264", "big") in VIDEO_CODEC_IDS


class TestParseFrameHeader:
    def test_parses_standard_media_packet(self) -> None:
        pts = 12_345_678
        size = 4096
        header = struct.pack(">QI", pts | KEY_FRAME_FLAG, size)
        parsed = parse_frame_header(header)
        assert isinstance(parsed, FrameMeta)
        assert parsed.pts_us == pts
        assert parsed.size == size
        assert parsed.key_frame is True
        assert parsed.config is False

    def test_parses_config_flag(self) -> None:
        header = struct.pack(">QI", CONFIG_PACKET_FLAG | 0, 200)
        parsed = parse_frame_header(header)
        assert isinstance(parsed, FrameMeta)
        assert parsed.config is True
        assert parsed.key_frame is False

    def test_parses_session_packet(self) -> None:
        # A session packet sets the top bit and packs flags(4) + width(4) +
        # height(4). The Java server uses a flags integer with bit 31 set as
        # the marker; the LSB optionally carries client_resized.
        flags = (SESSION_PACKET_FLAG >> 32) | 0x1
        width = 720
        height = 1280
        header = struct.pack(">III", flags, width, height)
        parsed = parse_frame_header(header)
        assert isinstance(parsed, SessionPacket)
        assert parsed.width == width
        assert parsed.height == height
        assert parsed.client_resized is True

    def test_rejects_wrong_size(self) -> None:
        with pytest.raises(ValueError):
            parse_frame_header(b"\x00" * 8)


class TestSerializeControlMessage:
    def test_inject_keycode_layout_matches_java_reader(self) -> None:
        # Wire layout per ControlMessageReader.parseInjectKeycode:
        #   type(1) action(1) keycode(i32 BE) repeat(i32 BE) metaState(i32 BE)
        msg = ControlMessage.inject_keycode(action=1, keycode=66, repeat=2, meta_state=0x10)
        wire = serialize_control_message(msg)
        assert wire == struct.pack(
            ">BBIII", CONTROL_MSG_TYPE.INJECT_KEYCODE, 1, 66, 2, 0x10
        )

    def test_inject_text_uses_utf8_with_truncation(self) -> None:
        text = "你好" * 200
        wire = serialize_control_message(ControlMessage.inject_text(text))
        assert wire[0] == CONTROL_MSG_TYPE.INJECT_TEXT
        size = struct.unpack(">I", wire[1:5])[0]
        body = wire[5:]
        assert size == len(body)
        # Up to 300 bytes, never breaks a UTF-8 boundary.
        assert len(body) <= 300
        assert body.decode("utf-8")  # no UnicodeDecodeError

    def test_inject_touch_event_layout_matches_c_client(self) -> None:
        # Mirror exactly the wire layout from app/src/control_msg.c::sc_control_msg_serialize:
        #   type(1) action(1) pointerId(u64 BE) x(i32 BE) y(i32 BE) sw(u16 BE)
        #   sh(u16 BE) pressure(u16 BE) actionButton(u32 BE) buttons(u32 BE)
        msg = ControlMessage.inject_touch_event(
            action=ACTION_DOWN,
            pointer_id=POINTER_ID_MOUSE,
            x=100,
            y=200,
            screen_width=720,
            screen_height=1280,
            pressure=1.0,
            action_button=BUTTON_PRIMARY,
            buttons=BUTTON_PRIMARY,
        )
        wire = serialize_control_message(msg)
        assert len(wire) == 32
        assert wire[0] == CONTROL_MSG_TYPE.INJECT_TOUCH_EVENT
        assert wire[1] == ACTION_DOWN
        assert struct.unpack(">Q", wire[2:10])[0] == POINTER_ID_MOUSE
        assert struct.unpack(">i", wire[10:14])[0] == 100
        assert struct.unpack(">i", wire[14:18])[0] == 200
        assert struct.unpack(">H", wire[18:20])[0] == 720
        assert struct.unpack(">H", wire[20:22])[0] == 1280
        # Pressure 1.0 maps to 0xFFFF (per Binary.u16FixedPointToFloat inverse).
        assert struct.unpack(">H", wire[22:24])[0] == 0xFFFF
        assert struct.unpack(">I", wire[24:28])[0] == BUTTON_PRIMARY
        assert struct.unpack(">I", wire[28:32])[0] == BUTTON_PRIMARY

    def test_inject_scroll_event_normalizes_to_unit_range(self) -> None:
        # The application API uses the [-16, 16] range; the wire format uses
        # [-1, 1] fixed-point (16-bit signed). 16.0 must clamp to 0x7FFF.
        msg = ControlMessage.inject_scroll_event(
            x=10,
            y=20,
            screen_width=720,
            screen_height=1280,
            h_scroll=0.0,
            v_scroll=16.0,
            buttons=0,
        )
        wire = serialize_control_message(msg)
        assert wire[0] == CONTROL_MSG_TYPE.INJECT_SCROLL_EVENT
        v_scroll_wire = struct.unpack(">H", wire[15:17])[0]
        # 0x7FFF is the maximum signed positive value (representing +1.0).
        assert v_scroll_wire == 0x7FFF

    def test_back_or_screen_on_is_two_bytes(self) -> None:
        wire = serialize_control_message(ControlMessage.back_or_screen_on(0))
        assert wire == struct.pack(">BB", CONTROL_MSG_TYPE.BACK_OR_SCREEN_ON, 0)

    def test_set_clipboard_layout(self) -> None:
        msg = ControlMessage.set_clipboard(sequence=0xAABBCCDD, text="hi", paste=True)
        wire = serialize_control_message(msg)
        assert wire[0] == CONTROL_MSG_TYPE.SET_CLIPBOARD
        assert struct.unpack(">Q", wire[1:9])[0] == 0xAABBCCDD
        assert wire[9] == 1
        assert struct.unpack(">I", wire[10:14])[0] == 2
        assert wire[14:] == b"hi"

    def test_set_display_power_on_off(self) -> None:
        on = serialize_control_message(ControlMessage.set_display_power(True))
        off = serialize_control_message(ControlMessage.set_display_power(False))
        assert on == struct.pack(">BB", CONTROL_MSG_TYPE.SET_DISPLAY_POWER, 1)
        assert off == struct.pack(">BB", CONTROL_MSG_TYPE.SET_DISPLAY_POWER, 0)

    def test_resize_display_layout(self) -> None:
        wire = serialize_control_message(
            ControlMessage(type=CONTROL_MSG_TYPE.RESIZE_DISPLAY, width=480, height=960)
        )
        assert wire == struct.pack(">BHH", CONTROL_MSG_TYPE.RESIZE_DISPLAY, 480, 960)

    def test_empty_messages_are_single_byte(self) -> None:
        for msg_type in (
            CONTROL_MSG_TYPE.EXPAND_NOTIFICATION_PANEL,
            CONTROL_MSG_TYPE.COLLAPSE_PANELS,
            CONTROL_MSG_TYPE.ROTATE_DEVICE,
            CONTROL_MSG_TYPE.RESET_VIDEO,
        ):
            wire = serialize_control_message(ControlMessage.empty(msg_type))
            assert wire == bytes([int(msg_type)])


class TestParseDeviceMessage:
    def test_parses_clipboard(self) -> None:
        text = "复制内容"
        raw = (
            bytes([DEVICE_MSG_TYPE.CLIPBOARD])
            + struct.pack(">I", len(text.encode("utf-8")))
            + text.encode("utf-8")
        )
        result = parse_device_message(_byte_reader(raw))
        assert result.type == DEVICE_MSG_TYPE.CLIPBOARD
        assert result.text == text

    def test_parses_ack_clipboard(self) -> None:
        raw = bytes([DEVICE_MSG_TYPE.ACK_CLIPBOARD]) + struct.pack(">Q", 0xABCD_1234)
        result = parse_device_message(_byte_reader(raw))
        assert result.type == DEVICE_MSG_TYPE.ACK_CLIPBOARD
        assert result.sequence == 0xABCD_1234

    def test_parses_uhid_output(self) -> None:
        payload = b"\x10\x20\x30"
        raw = (
            bytes([DEVICE_MSG_TYPE.UHID_OUTPUT])
            + struct.pack(">HH", 7, len(payload))
            + payload
        )
        result = parse_device_message(_byte_reader(raw))
        assert result.uhid_id == 7
        assert result.data == payload


def _byte_reader(buffer: bytes):
    """Return a ``read_exact``-style callable backed by ``buffer``."""
    state = {"offset": 0}

    def read(n: int) -> bytes:
        start = state["offset"]
        end = start + n
        if end > len(buffer):
            raise EOFError("read past end of buffer")
        state["offset"] = end
        return buffer[start:end]

    return read
