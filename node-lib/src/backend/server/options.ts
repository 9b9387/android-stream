import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { AudioCodec, VideoCodec } from "../../protocol/index.js";
import { getScrcpyProtocol } from "../../protocol/registry.js";
import {
  ScrcpyProtocol,
  ScrcpyProtocolVersion,
} from "../../protocol/core/types.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export interface ScrcpyBackendOptions {
  deviceSerial?: string;
  protocolVersion?: ScrcpyProtocolVersion;
  scid?: number;
  maxSize?: number;
  maxFps?: number;
  videoBitRate?: number;
  audioBitRate?: number;
  video?: boolean;
  audio?: boolean;
  control?: boolean;
  videoCodec?: VideoCodec;
  audioCodec?: AudioCodec;
  connectionTimeoutMs?: number;
  deployTimeoutMs?: number;
  pushTimeoutMs?: number;
  serverJarPath?: string;
}

export interface NormalizedScrcpyBackendOptions extends Required<
  Omit<ScrcpyBackendOptions, "serverJarPath">
> {
  protocol: ScrcpyProtocol;
  socketName: string;
  serverJarPath: string;
}

export function normalizeBackendOptions(
  options: ScrcpyBackendOptions = {},
): NormalizedScrcpyBackendOptions {
  const protocolVersion = options.protocolVersion ?? "4.0";
  const protocol = getScrcpyProtocol(protocolVersion);
  const scid = options.scid ?? 0x0000000a;

  return {
    deviceSerial: options.deviceSerial ?? "",
    protocolVersion,
    protocol,
    scid,
    socketName: protocol.getSocketName(scid),
    maxSize: options.maxSize ?? 0,
    maxFps: options.maxFps ?? 30,
    videoBitRate: options.videoBitRate ?? 8000000,
    audioBitRate: options.audioBitRate ?? 128000,
    video: options.video !== false,
    audio: options.audio !== false,
    control: options.control !== false,
    videoCodec: options.videoCodec ?? VideoCodec.H264,
    audioCodec: options.audioCodec ?? AudioCodec.OPUS,
    connectionTimeoutMs: options.connectionTimeoutMs ?? 8000,
    deployTimeoutMs: options.deployTimeoutMs ?? 5000,
    pushTimeoutMs: options.pushTimeoutMs ?? 60000,
    serverJarPath:
      options.serverJarPath ??
      path.join(
        __dirname,
        "..",
        "..",
        "..",
        "assets",
        protocol.serverJarAssetName,
      ),
  };
}
