import { NormalizedScrcpyBackendOptions } from "./options.js";

export function getDeviceServerPath(serverVersion: string): string {
  return `/data/local/tmp/scrcpy-server-v${serverVersion}.jar`;
}

export function buildServerCommand(
  deviceServerPath: string,
  options: NormalizedScrcpyBackendOptions,
): string {
  const args = [
    `CLASSPATH=${deviceServerPath}`,
    "app_process",
    "/",
    "com.genymobile.scrcpy.Server",
    ...options.protocol.buildServerOptions({
      scid: options.scid,
      video: options.video,
      audio: options.audio,
      control: options.control,
      videoCodec: options.videoCodec,
      audioCodec: options.audioCodec,
      maxSize: options.maxSize,
      maxFps: options.maxFps,
      videoBitRate: options.videoBitRate,
      audioBitRate: options.audioBitRate,
    }),
  ];
  return args.join(" ");
}
