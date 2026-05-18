import { MediaKind, MediaPacket } from "../service/types.js";
import {
  WEBSOCKET_HEADER_SIZE,
  WEBSOCKET_KIND_AUDIO,
  WEBSOCKET_KIND_SESSION,
  WEBSOCKET_KIND_VIDEO,
} from "./types.js";

export function serializeMediaPacket(packet: MediaPacket): Uint8Array {
  let kind = WEBSOCKET_KIND_VIDEO;
  let flags = 0;
  let pts = packet.ptsUs;
  let size = packet.payload.length;
  let payload = packet.payload;

  if (packet.config) flags |= 0x01;
  if (packet.keyFrame) flags |= 0x02;

  if (packet.kind === MediaKind.AUDIO) {
    kind = WEBSOCKET_KIND_AUDIO;
  } else if (packet.kind === MediaKind.SESSION) {
    kind = WEBSOCKET_KIND_SESSION;
    size = 0;
    payload = new Uint8Array(0);
    // The browser bridge uses the 64-bit timestamp slot for session dimensions:
    // high 32 bits = width, low 32 bits = height.
    pts = (BigInt(packet.width ?? 0) << 32n) | BigInt(packet.height ?? 0);
  }

  const buf = new Uint8Array(WEBSOCKET_HEADER_SIZE + payload.byteLength);
  const view = new DataView(buf.buffer);
  view.setUint8(0, kind);
  view.setUint8(1, flags);
  view.setUint16(2, 0);
  view.setUint32(4, size);
  view.setBigUint64(8, pts);
  buf.set(payload, WEBSOCKET_HEADER_SIZE);
  return buf;
}
