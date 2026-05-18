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
  return [
    SCRCPY_4_0_SERVER_VERSION,
    `log_level=${input.logLevel ?? "info"}`,
    "tunnel_forward=true",
    `scid=${scid.toString(16)}`,
    `video=${input.video}`,
    `audio=${input.audio}`,
    `control=${input.control}`,
    `video_codec=${input.videoCodec}`,
    `audio_codec=${input.audioCodec}`,
    `max_size=${input.maxSize}`,
    `max_fps=${input.maxFps}`,
    `video_bit_rate=${input.videoBitRate}`,
    `audio_bit_rate=${input.audioBitRate}`,
    "send_device_meta=true",
    "send_frame_meta=true",
    "send_dummy_byte=true",
    "send_stream_meta=true",
    "cleanup=true",
    "stay_awake=false",
    "show_touches=false",
    "power_off_on_close=false",
    "clipboard_autosync=false",
  ];
}
