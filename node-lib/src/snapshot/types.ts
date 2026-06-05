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
  /**
   * Max time to wait for ffmpeg stdin to drain before treating the process as
   * stalled and tearing it down. Defaults to 5000ms.
   */
  drainTimeoutMs?: number;
  /**
   * Grace period after SIGTERM before escalating to SIGKILL on stop. Defaults
   * to 2000ms.
   */
  killTimeoutMs?: number;
  /**
   * Emit a `stale` event when the newest snapshot is older than this many ms
   * (e.g. the device stopped producing frames). 0 disables. Defaults to
   * 10000ms.
   */
  staleTimeoutMs?: number;
  /**
   * Cap on the internal ffmpeg stdout reassembly buffer, guarding against a
   * corrupt stream with no JPEG end marker. Defaults to 16MiB.
   */
  maxStdoutBytes?: number;
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
