import { dataView, utf8Encode } from "../core/binary.js";

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

export function getCodecId(name: string): number {
  const bytes = new Uint8Array(4);
  const nameBytes = utf8Encode(name);
  bytes.set(nameBytes, 4 - nameBytes.byteLength);
  return dataView(bytes).getUint32(0);
}

export const VIDEO_CODEC_IDS: Record<number, VideoCodec> = Object.fromEntries(
  Object.values(VideoCodec).map((codec) => [getCodecId(codec), codec]),
);

export const AUDIO_CODEC_IDS: Record<number, AudioCodec> = Object.fromEntries(
  Object.values(AudioCodec).map((codec) => [getCodecId(codec), codec]),
);
