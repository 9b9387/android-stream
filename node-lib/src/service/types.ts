import { ScrcpyBackendOptions } from "../backend/server/options.js";

export enum StreamState {
  STOPPED = "stopped",
  STARTING = "starting",
  RUNNING = "running",
  STOPPING = "stopping",
  ERROR = "error",
}

export enum MediaKind {
  VIDEO = "video",
  AUDIO = "audio",
  SESSION = "session",
}

export interface MediaPacket {
  kind: MediaKind;
  ptsUs: bigint;
  config: boolean;
  keyFrame: boolean;
  payload: Uint8Array;
  width?: number;
  height?: number;
}

export interface ScrcpyStreamServiceOptions extends ScrcpyBackendOptions {
  queueMaxPackets?: number;
}
