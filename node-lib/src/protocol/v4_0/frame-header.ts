import { dataView } from "../core/binary.js";

export const SESSION_PACKET_FLAG = 1n << 63n;
export const CONFIG_PACKET_FLAG = 1n << 62n;
export const KEY_FRAME_FLAG = 1n << 61n;
export const PTS_MASK = (1n << 61n) - 1n;

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

export type StreamPacketMeta = SessionPacket | FrameMeta;

export function parseFrameHeader(header: Uint8Array): StreamPacketMeta {
  if (header.byteLength !== 12) {
    throw new Error(`frame header must be 12 bytes, got ${header.byteLength}`);
  }

  const view = dataView(header);
  const ptsAndFlags = view.getBigUint64(0);
  const size = view.getUint32(8);

  if (ptsAndFlags & SESSION_PACKET_FLAG) {
    const flagsHigh = Number((ptsAndFlags >> 32n) & 0xffffffffn);
    return {
      kind: "session",
      width: Number(ptsAndFlags & 0xffffffffn),
      height: size,
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
