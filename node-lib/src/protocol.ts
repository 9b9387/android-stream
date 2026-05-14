import { Buffer } from "node:buffer";

// ---------------------------------------------------------------------------
// Stream meta constants
// ---------------------------------------------------------------------------

export const SESSION_PACKET_FLAG = 1n << 63n;
export const CONFIG_PACKET_FLAG = 1n << 62n;
export const KEY_FRAME_FLAG = 1n << 61n;
export const PTS_MASK = (1n << 61n) - 1n;

export enum VideoCodec {
  H264 = "h264",
  H265 = "h265",
  AV1 = "av1",
}

export enum AudioCodec {
  OPUS = "opus",
  AAC = "aac",
  FLAC = "flac",
  RAW = "raw",
}

function getCodecId(name: string): number {
  const buf = Buffer.alloc(4, 0);
  const nameBuf = Buffer.from(name, "ascii");
  nameBuf.copy(buf, 4 - nameBuf.length);
  return buf.readUInt32BE(0);
}

export const VIDEO_CODEC_IDS: Record<number, VideoCodec> = Object.fromEntries(
  Object.values(VideoCodec).map((c) => [getCodecId(c), c])
);

export const AUDIO_CODEC_IDS: Record<number, AudioCodec> = Object.fromEntries(
  Object.values(AudioCodec).map((c) => [getCodecId(c), c])
);

// ---------------------------------------------------------------------------
// Frame headers
// ---------------------------------------------------------------------------

export interface SessionPacket {
  kind: "session";
  width: number;
  height: number;
  clientResized: boolean;
}

export interface FrameMeta {
  kind: "frame";
  ptsUs: bigint;
  size: number;
  config: boolean;
  keyFrame: boolean;
}

export function parseFrameHeader(header: Buffer): SessionPacket | FrameMeta {
  if (header.length !== 12) {
    throw new Error(`frame header must be 12 bytes, got ${header.length}`);
  }

  const ptsAndFlags = header.readBigUInt64BE(0);
  const size = header.readUInt32BE(8);

  if (ptsAndFlags & SESSION_PACKET_FLAG) {
    const flagsHigh = Number((ptsAndFlags >> 32n) & 0xffffffffn);
    const width = Number(ptsAndFlags & 0xffffffffn);
    const height = size;
    return {
      kind: "session",
      width,
      height,
      clientResized: (flagsHigh & 0x01) !== 0,
    };
  }

  return {
    kind: "frame",
    ptsUs: ptsAndFlags & PTS_MASK,
    size,
    config: (ptsAndFlags & CONFIG_PACKET_FLAG) !== 0n,
    keyFrame: (ptsAndFlags & KEY_FRAME_FLAG) !== 0n,
  };
}

// ---------------------------------------------------------------------------
// Control messages (client -> device)
// ---------------------------------------------------------------------------

export enum ControlMessageType {
  INJECT_KEYCODE = 0,
  INJECT_TEXT = 1,
  INJECT_TOUCH_EVENT = 2,
  INJECT_SCROLL_EVENT = 3,
  BACK_OR_SCREEN_ON = 4,
  EXPAND_NOTIFICATION_PANEL = 5,
  EXPAND_SETTINGS_PANEL = 6,
  COLLAPSE_PANELS = 7,
  GET_CLIPBOARD = 8,
  SET_CLIPBOARD = 9,
  SET_DISPLAY_POWER = 10,
  ROTATE_DEVICE = 11,
  UHID_CREATE = 12,
  UHID_INPUT = 13,
  UHID_DESTROY = 14,
  OPEN_HARD_KEYBOARD_SETTINGS = 15,
  START_APP = 16,
  RESET_VIDEO = 17,
  CAMERA_SET_TORCH = 18,
  CAMERA_ZOOM_IN = 19,
  CAMERA_ZOOM_OUT = 20,
  RESIZE_DISPLAY = 21,
}

export const ACTION_DOWN = 0;
export const ACTION_UP = 1;
export const ACTION_MOVE = 2;

export const KEY_ACTION_DOWN = 0;
export const KEY_ACTION_UP = 1;

export const BUTTON_PRIMARY = 1 << 0;
export const BUTTON_SECONDARY = 1 << 1;
export const BUTTON_TERTIARY = 1 << 2;

export const POINTER_ID_MOUSE = -1n; // 0xFFFF_FFFF_FFFF_FFFF as signed i64
export const POINTER_ID_GENERIC_FINGER = -2n; // 0xFFFF_FFFF_FFFF_FFFE

export const INJECT_TEXT_MAX_LENGTH = 300;
export const SET_CLIPBOARD_TEXT_MAX_LENGTH = (1 << 18) - 1;

function u16FixedPoint(value: number): number {
  if (value >= 1.0) return 0xffff;
  if (value <= 0.0) return 0;
  return Math.floor(value * 0x10000) & 0xffff;
}

function i16FixedPoint(value: number): number {
  if (value >= 1.0) return 0x7fff;
  if (value <= -1.0) return 0x8000; // stored as unsigned 16-bit
  return Math.floor(value * 0x8000) & 0xffff;
}

export interface ControlMessage {
  type: ControlMessageType;
  action?: number;
  keycode?: number;
  repeat?: number;
  metaState?: number;
  pointerId?: bigint;
  pressure?: number;
  actionButton?: number;
  buttons?: number;
  x?: number;
  y?: number;
  screenWidth?: number;
  screenHeight?: number;
  hScroll?: number;
  vScroll?: number;
  text?: string;
  paste?: boolean;
  sequence?: bigint;
  copyKey?: number;
  on?: boolean;
  width?: number;
  height?: number;
}

export function serializeControlMessage(msg: ControlMessage): Buffer {
  const type = msg.type;
  switch (type) {
    case ControlMessageType.INJECT_KEYCODE: {
      const buf = Buffer.alloc(14);
      buf.writeUInt8(type, 0);
      buf.writeUInt8((msg.action || 0) & 0xff, 1);
      buf.writeUInt32BE(msg.keycode || 0, 2);
      buf.writeUInt32BE(msg.repeat || 0, 6);
      buf.writeUInt32BE(msg.metaState || 0, 10);
      return buf;
    }
    case ControlMessageType.INJECT_TEXT: {
      const textBuf = truncateUtf8(msg.text || "", INJECT_TEXT_MAX_LENGTH);
      const buf = Buffer.alloc(5 + textBuf.length);
      buf.writeUInt8(type, 0);
      buf.writeUInt32BE(textBuf.length, 1);
      textBuf.copy(buf, 5);
      return buf;
    }
    case ControlMessageType.INJECT_TOUCH_EVENT: {
      const buf = Buffer.alloc(32);
      buf.writeUInt8(type, 0);
      buf.writeUInt8((msg.action || 0) & 0xff, 1);
      buf.writeBigUInt64BE(BigInt.asUintN(64, msg.pointerId || 0n), 2);
      buf.writeInt32BE(Math.floor(msg.x || 0), 10);
      buf.writeInt32BE(Math.floor(msg.y || 0), 14);
      buf.writeUInt16BE((msg.screenWidth || 0) & 0xffff, 18);
      buf.writeUInt16BE((msg.screenHeight || 0) & 0xffff, 20);
      buf.writeUInt16BE(u16FixedPoint(msg.pressure ?? 1.0), 22);
      buf.writeUInt32BE((msg.actionButton || 0) & 0xffffffff, 24);
      buf.writeUInt32BE((msg.buttons || 0) & 0xffffffff, 28);
      return buf;
    }
    case ControlMessageType.INJECT_SCROLL_EVENT: {
      const buf = Buffer.alloc(21);
      buf.writeUInt8(type, 0);
      buf.writeInt32BE(Math.floor(msg.x || 0), 1);
      buf.writeInt32BE(Math.floor(msg.y || 0), 5);
      buf.writeUInt16BE((msg.screenWidth || 0) & 0xffff, 9);
      buf.writeUInt16BE((msg.screenHeight || 0) & 0xffff, 11);
      const h = Math.max(-1.0, Math.min(1.0, (msg.hScroll || 0.0) / 16.0));
      const v = Math.max(-1.0, Math.min(1.0, (msg.vScroll || 0.0) / 16.0));
      buf.writeUInt16BE(i16FixedPoint(h), 13);
      buf.writeUInt16BE(i16FixedPoint(v), 15);
      buf.writeUInt32BE((msg.buttons || 0) & 0xffffffff, 17);
      return buf;
    }
    case ControlMessageType.BACK_OR_SCREEN_ON: {
      const buf = Buffer.alloc(2);
      buf.writeUInt8(type, 0);
      buf.writeUInt8((msg.action || 0) & 0xff, 1);
      return buf;
    }
    case ControlMessageType.SET_CLIPBOARD: {
      const textBuf = truncateUtf8(
        msg.text || "",
        SET_CLIPBOARD_TEXT_MAX_LENGTH
      );
      const buf = Buffer.alloc(14 + textBuf.length);
      buf.writeUInt8(type, 0);
      buf.writeBigUInt64BE(BigInt.asUintN(64, msg.sequence || 0n), 1);
      buf.writeUInt8(msg.paste ? 1 : 0, 9);
      buf.writeUInt32BE(textBuf.length, 10);
      textBuf.copy(buf, 14);
      return buf;
    }
    case ControlMessageType.SET_DISPLAY_POWER:
    case ControlMessageType.CAMERA_SET_TORCH: {
      const buf = Buffer.alloc(2);
      buf.writeUInt8(type, 0);
      buf.writeUInt8(msg.on ? 1 : 0, 1);
      return buf;
    }
    case ControlMessageType.RESIZE_DISPLAY: {
      const buf = Buffer.alloc(5);
      buf.writeUInt8(type, 0);
      buf.writeUInt16BE((msg.width || 0) & 0xffff, 1);
      buf.writeUInt16BE((msg.height || 0) & 0xffff, 3);
      return buf;
    }
    case ControlMessageType.EXPAND_NOTIFICATION_PANEL:
    case ControlMessageType.EXPAND_SETTINGS_PANEL:
    case ControlMessageType.COLLAPSE_PANELS:
    case ControlMessageType.ROTATE_DEVICE:
    case ControlMessageType.OPEN_HARD_KEYBOARD_SETTINGS:
    case ControlMessageType.RESET_VIDEO:
    case ControlMessageType.CAMERA_ZOOM_IN:
    case ControlMessageType.CAMERA_ZOOM_OUT: {
      const buf = Buffer.alloc(1);
      buf.writeUInt8(type, 0);
      return buf;
    }
    default:
      throw new Error(`unsupported control message type: ${type}`);
  }
}

function truncateUtf8(text: string, maxBytes: number): Buffer {
  const buf = Buffer.from(text, "utf-8");
  if (buf.length <= maxBytes) return buf;

  let end = maxBytes;
  // If the last byte is a continuation byte (10xxxxxx), move back
  while (end > 0 && (buf[end] & 0xc0) === 0x80) {
    end--;
  }
  // If the last byte is a leading byte of a multi-byte sequence, drop it
  if (end > 0 && (buf[end] & 0x80) !== 0) {
    end--;
  }
  return buf.subarray(0, end);
}

// ---------------------------------------------------------------------------
// Device messages (device -> client)
// ---------------------------------------------------------------------------

export enum DeviceMessageType {
  CLIPBOARD = 0,
  ACK_CLIPBOARD = 1,
  UHID_OUTPUT = 2,
}

export interface DeviceMessage {
  type: DeviceMessageType;
  text?: string;
  sequence?: bigint;
  uhidId?: number;
  data?: Buffer;
}

export async function parseDeviceMessage(
  readExact: (n: number) => Promise<Buffer>
): Promise<DeviceMessage> {
  const head = await readExact(1);
  const type = head[0] as DeviceMessageType;

  switch (type) {
    case DeviceMessageType.CLIPBOARD: {
      const lenBuf = await readExact(4);
      const length = lenBuf.readUInt32BE(0);
      const textBuf = await readExact(length);
      return { type, text: textBuf.toString("utf-8") };
    }
    case DeviceMessageType.ACK_CLIPBOARD: {
      const seqBuf = await readExact(8);
      return { type, sequence: seqBuf.readBigUInt64BE(0) };
    }
    case DeviceMessageType.UHID_OUTPUT: {
      const metaBuf = await readExact(4);
      const uhidId = metaBuf.readUInt16BE(0);
      const length = metaBuf.readUInt16BE(2);
      const data = await readExact(length);
      return { type, uhidId, data };
    }
    default:
      throw new Error(`unknown device message type: ${type}`);
  }
}
