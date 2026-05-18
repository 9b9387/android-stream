import { ChildProcessWithoutNullStreams } from "node:child_process";

export interface Snapshot {
  contentType: "image/jpeg";
  data: Buffer<ArrayBufferLike>;
  timestampMs: number;
}

export interface SnapshotCacheOptions {
  enabled?: boolean;
  fps?: number;
  quality?: number;
  ffmpegPath?: string;
  spawnProcess?: FfmpegProcessFactory;
}

export type FfmpegProcess = Pick<
  ChildProcessWithoutNullStreams,
  "stdin" | "stdout" | "stderr" | "kill" | "on" | "once"
>;

export type FfmpegProcessFactory = (
  command: string,
  args: string[],
) => FfmpegProcess;
