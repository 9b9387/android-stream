import { utf8Encode } from "../core/binary.js";

export function truncateUtf8(text: string, maxBytes: number): Uint8Array {
  const bytes = utf8Encode(text);
  if (bytes.byteLength <= maxBytes) return bytes;

  let end = maxBytes;
  while (end > 0 && (bytes[end] & 0xc0) === 0x80) {
    end--;
  }
  if (end > 0 && (bytes[end] & 0x80) !== 0) {
    end--;
  }
  return bytes.subarray(0, end);
}

export function writeTinyString(
  target: Uint8Array,
  offset: number,
  text: string,
  maxBytes: number,
): number {
  if (maxBytes > 0xff) {
    throw new Error(
      `tiny string max length must fit in one byte, got ${maxBytes}`,
    );
  }

  const payload = truncateUtf8(text, maxBytes);
  target[offset] = payload.byteLength;
  target.set(payload, offset + 1);
  return 1 + payload.byteLength;
}
