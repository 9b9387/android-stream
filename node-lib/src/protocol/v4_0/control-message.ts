import {
  ControlMessage,
  ControlMessageType,
  INJECT_TEXT_MAX_LENGTH,
  SET_CLIPBOARD_TEXT_MAX_LENGTH,
  START_APP_NAME_MAX_LENGTH,
  UHID_NAME_MAX_LENGTH,
} from "./control-message-types.js";
import { truncateUtf8, writeTinyString } from "./string-payload.js";

function u16FixedPoint(value: number): number {
  if (value >= 1.0) return 0xffff;
  if (value <= 0.0) return 0;
  return Math.floor(value * 0x10000) & 0xffff;
}

function i16FixedPoint(value: number): number {
  if (value >= 1.0) return 0x7fff;
  if (value <= -1.0) return 0x8000;
  return Math.floor(value * 0x8000) & 0xffff;
}

export function serializeControlMessage(msg: ControlMessage): Uint8Array {
  switch (msg.type) {
    case ControlMessageType.INJECT_KEYCODE: {
      const buf = new Uint8Array(14);
      const view = new DataView(buf.buffer);
      view.setUint8(0, msg.type);
      view.setUint8(1, msg.action & 0xff);
      view.setUint32(2, msg.keycode >>> 0);
      view.setUint32(6, (msg.repeat ?? 0) >>> 0);
      view.setUint32(10, (msg.metaState ?? 0) >>> 0);
      return buf;
    }
    case ControlMessageType.INJECT_TEXT: {
      const textBuf = truncateUtf8(msg.text, INJECT_TEXT_MAX_LENGTH);
      const buf = new Uint8Array(5 + textBuf.byteLength);
      const view = new DataView(buf.buffer);
      view.setUint8(0, msg.type);
      view.setUint32(1, textBuf.byteLength);
      buf.set(textBuf, 5);
      return buf;
    }
    case ControlMessageType.INJECT_TOUCH_EVENT: {
      const buf = new Uint8Array(32);
      const view = new DataView(buf.buffer);
      view.setUint8(0, msg.type);
      view.setUint8(1, msg.action & 0xff);
      view.setBigUint64(2, BigInt.asUintN(64, msg.pointerId));
      view.setInt32(10, Math.floor(msg.x));
      view.setInt32(14, Math.floor(msg.y));
      view.setUint16(18, msg.screenWidth & 0xffff);
      view.setUint16(20, msg.screenHeight & 0xffff);
      view.setUint16(22, u16FixedPoint(msg.pressure ?? 1.0));
      view.setUint32(24, (msg.actionButton ?? 0) >>> 0);
      view.setUint32(28, (msg.buttons ?? 0) >>> 0);
      return buf;
    }
    case ControlMessageType.INJECT_SCROLL_EVENT: {
      const buf = new Uint8Array(21);
      const view = new DataView(buf.buffer);
      view.setUint8(0, msg.type);
      view.setInt32(1, Math.floor(msg.x));
      view.setInt32(5, Math.floor(msg.y));
      view.setUint16(9, msg.screenWidth & 0xffff);
      view.setUint16(11, msg.screenHeight & 0xffff);
      const h = Math.max(-1.0, Math.min(1.0, (msg.hScroll ?? 0.0) / 16.0));
      const v = Math.max(-1.0, Math.min(1.0, (msg.vScroll ?? 0.0) / 16.0));
      view.setUint16(13, i16FixedPoint(h));
      view.setUint16(15, i16FixedPoint(v));
      view.setUint32(17, (msg.buttons ?? 0) >>> 0);
      return buf;
    }
    case ControlMessageType.BACK_OR_SCREEN_ON: {
      return Uint8Array.of(msg.type, msg.action & 0xff);
    }
    case ControlMessageType.GET_CLIPBOARD: {
      return Uint8Array.of(msg.type, msg.copyKey & 0xff);
    }
    case ControlMessageType.SET_CLIPBOARD: {
      const textBuf = truncateUtf8(msg.text, SET_CLIPBOARD_TEXT_MAX_LENGTH);
      const buf = new Uint8Array(14 + textBuf.byteLength);
      const view = new DataView(buf.buffer);
      view.setUint8(0, msg.type);
      view.setBigUint64(1, BigInt.asUintN(64, msg.sequence));
      view.setUint8(9, msg.paste ? 1 : 0);
      view.setUint32(10, textBuf.byteLength);
      buf.set(textBuf, 14);
      return buf;
    }
    case ControlMessageType.SET_DISPLAY_POWER:
    case ControlMessageType.CAMERA_SET_TORCH: {
      return Uint8Array.of(msg.type, msg.on ? 1 : 0);
    }
    case ControlMessageType.UHID_CREATE: {
      const nameBuf = truncateUtf8(msg.name, UHID_NAME_MAX_LENGTH);
      const reportDescriptor = msg.reportDescriptor;
      const buf = new Uint8Array(
        10 + nameBuf.byteLength + reportDescriptor.byteLength,
      );
      const view = new DataView(buf.buffer);
      let offset = 0;
      view.setUint8(offset++, msg.type);
      view.setUint16(offset, msg.id & 0xffff);
      offset += 2;
      view.setUint16(offset, msg.vendorId & 0xffff);
      offset += 2;
      view.setUint16(offset, msg.productId & 0xffff);
      offset += 2;
      offset += writeTinyString(buf, offset, msg.name, UHID_NAME_MAX_LENGTH);
      view.setUint16(offset, reportDescriptor.byteLength & 0xffff);
      offset += 2;
      buf.set(reportDescriptor, offset);
      return buf;
    }
    case ControlMessageType.UHID_INPUT: {
      const buf = new Uint8Array(5 + msg.data.byteLength);
      const view = new DataView(buf.buffer);
      view.setUint8(0, msg.type);
      view.setUint16(1, msg.id & 0xffff);
      view.setUint16(3, msg.data.byteLength & 0xffff);
      buf.set(msg.data, 5);
      return buf;
    }
    case ControlMessageType.UHID_DESTROY: {
      const buf = new Uint8Array(3);
      const view = new DataView(buf.buffer);
      view.setUint8(0, msg.type);
      view.setUint16(1, msg.id & 0xffff);
      return buf;
    }
    case ControlMessageType.START_APP: {
      const nameBuf = truncateUtf8(msg.name, START_APP_NAME_MAX_LENGTH);
      const buf = new Uint8Array(2 + nameBuf.byteLength);
      new DataView(buf.buffer).setUint8(0, msg.type);
      writeTinyString(buf, 1, msg.name, START_APP_NAME_MAX_LENGTH);
      return buf;
    }
    case ControlMessageType.RESIZE_DISPLAY: {
      const buf = new Uint8Array(5);
      const view = new DataView(buf.buffer);
      view.setUint8(0, msg.type);
      view.setUint16(1, msg.width & 0xffff);
      view.setUint16(3, msg.height & 0xffff);
      return buf;
    }
    case ControlMessageType.EXPAND_NOTIFICATION_PANEL:
    case ControlMessageType.EXPAND_SETTINGS_PANEL:
    case ControlMessageType.COLLAPSE_PANELS:
    case ControlMessageType.ROTATE_DEVICE:
    case ControlMessageType.OPEN_HARD_KEYBOARD_SETTINGS:
    case ControlMessageType.RESET_VIDEO:
    case ControlMessageType.CAMERA_ZOOM_IN:
    case ControlMessageType.CAMERA_ZOOM_OUT:
      return Uint8Array.of(msg.type);
  }
}
