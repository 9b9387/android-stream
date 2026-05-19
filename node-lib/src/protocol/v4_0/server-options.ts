import { AudioCodec, VideoCodec } from "./codecs.js";

export const SCRCPY_4_0_SERVER_VERSION = "4.0";
export const SCRCPY_4_0_SERVER_JAR = "scrcpy-server-v4.0.jar";
export const DEFAULT_SCRCPY_SCID = 0x0000000a;

export interface ScrcpyServerOptionInput {
  scid?: number;
  logLevel?: "verbose" | "debug" | "info" | "warn" | "error";
  video: boolean;
  audio: boolean;
  control: boolean;
  videoCodec: VideoCodec;
  audioCodec: AudioCodec;
  videoEncoder?: string;
  maxSize: number;
  maxFps: number;
  videoBitRate: number;
  audioBitRate: number;
}

export function getSocketName(scid = DEFAULT_SCRCPY_SCID): string {
  if (scid === -1) return "scrcpy";
  if (scid < -1) {
    throw new Error(
      `scrcpy scid must be -1 or a non-negative 31-bit value, got ${scid}`,
    );
  }
  return `scrcpy_${scid.toString(16).padStart(8, "0")}`;
}

export function buildServerOptions(input: ScrcpyServerOptionInput): string[] {
  const scid = input.scid ?? DEFAULT_SCRCPY_SCID;
  const opts = [
    SCRCPY_4_0_SERVER_VERSION,
    `scid=${(scid >>> 0).toString(16).padStart(8, "0")}`,
    `log_level=${input.logLevel ?? "info"}`,
    "tunnel_forward=true",
    `video=${input.video}`,
    `audio=${input.audio}`,
    `control=${input.control}`,
  ];

  if (input.videoCodec) {
    opts.push(`video_codec=${input.videoCodec}`);
  }
  if (input.audioCodec) {
    opts.push(`audio_codec=${input.audioCodec}`);
  }
  if (input.videoEncoder) {
    opts.push(`video_encoder=${input.videoEncoder}`);
  }
  if (input.maxSize > 0) {
    opts.push(`max_size=${input.maxSize}`);
  }
  if (input.maxFps > 0) {
    opts.push(`max_fps=${input.maxFps}`);
  }
  if (input.videoBitRate > 0 && input.videoBitRate !== 8000000) {
    opts.push(`video_bit_rate=${input.videoBitRate}`);
  }
  if (input.audioBitRate > 0 && input.audioBitRate !== 128000) {
    opts.push(`audio_bit_rate=${input.audioBitRate}`);
  }

  // Other options like cleanup, send_device_meta, etc. use server-side defaults
  // to keep the command line short and avoid Samsung-specific crashes.
  return opts;
}
