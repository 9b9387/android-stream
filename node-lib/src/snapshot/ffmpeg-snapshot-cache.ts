import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import {
  MediaKind,
  MediaPacket,
  ScrcpyStreamService,
  StreamState,
} from "../service/index.js";
import { VideoCodec } from "../protocol/index.js";
import { FfmpegProcess, Snapshot, SnapshotCacheOptions } from "./types.js";

const JPEG_SOI = Buffer.from([0xff, 0xd8]);
const JPEG_EOI = Buffer.from([0xff, 0xd9]);
const DEFAULT_SNAPSHOT_FPS = 2;
const DEFAULT_JPEG_QUALITY = 85;
const DEFAULT_DRAIN_TIMEOUT_MS = 5000;
const DEFAULT_KILL_TIMEOUT_MS = 2000;
const DEFAULT_STALE_TIMEOUT_MS = 10000;
const DEFAULT_MAX_STDOUT_BYTES = 16 * 1024 * 1024;
const STALE_CHECK_INTERVAL_MS = 1000;

export interface JpegExtractionResult {
  frames: Buffer<ArrayBufferLike>[];
  remainder: Buffer<ArrayBufferLike>;
}

export function extractJpegFrames(
  buffer: Buffer<ArrayBufferLike>,
): JpegExtractionResult {
  const frames: Buffer<ArrayBufferLike>[] = [];
  let offset = 0;

  while (offset < buffer.length) {
    const start = buffer.indexOf(JPEG_SOI, offset);
    if (start === -1) {
      const keep =
        buffer[buffer.length - 1] === 0xff
          ? buffer.subarray(-1)
          : Buffer.alloc(0);
      return { frames, remainder: keep };
    }

    const end = buffer.indexOf(JPEG_EOI, start + JPEG_SOI.length);
    if (end === -1) {
      return { frames, remainder: buffer.subarray(start) };
    }

    const frameEnd = end + JPEG_EOI.length;
    frames.push(Buffer.from(buffer.subarray(start, frameEnd)));
    offset = frameEnd;
  }

  return { frames, remainder: Buffer.alloc(0) };
}

export class FfmpegSnapshotCache extends EventEmitter {
  private readonly enabled: boolean;
  private readonly fps: number;
  private readonly quality: number;
  private readonly ffmpegPath: string;
  private readonly drainTimeoutMs: number;
  private readonly killTimeoutMs: number;
  private readonly staleTimeoutMs: number;
  private readonly maxStdoutBytes: number;
  private readonly spawnProcess: (
    command: string,
    args: string[],
  ) => FfmpegProcess;

  private process: FfmpegProcess | null = null;
  private subscription: AsyncIterableIterator<MediaPacket> | null = null;
  private stdoutBuffer: Buffer<ArrayBufferLike> = Buffer.alloc(0);
  private stderrTail = "";
  private currentSnapshot: Snapshot | null = null;
  private running = false;
  private staleTimer: NodeJS.Timeout | null = null;
  private staleEmitted = false;
  private readonly onServiceState: (state: StreamState) => void;

  constructor(
    private readonly service: ScrcpyStreamService,
    options: SnapshotCacheOptions = {},
  ) {
    super();
    this.enabled = options.enabled !== false;
    this.fps = normalizeFps(options.fps ?? DEFAULT_SNAPSHOT_FPS);
    this.quality = normalizeJpegQuality(
      options.quality ?? DEFAULT_JPEG_QUALITY,
    );
    this.ffmpegPath = options.ffmpegPath ?? "ffmpeg";
    this.drainTimeoutMs = options.drainTimeoutMs ?? DEFAULT_DRAIN_TIMEOUT_MS;
    this.killTimeoutMs = options.killTimeoutMs ?? DEFAULT_KILL_TIMEOUT_MS;
    this.staleTimeoutMs = options.staleTimeoutMs ?? DEFAULT_STALE_TIMEOUT_MS;
    this.maxStdoutBytes = options.maxStdoutBytes ?? DEFAULT_MAX_STDOUT_BYTES;
    this.spawnProcess =
      options.spawnProcess ??
      ((command, args) =>
        spawn(command, args, {
          stdio: ["pipe", "pipe", "pipe"],
        }));
    this.onServiceState = (state: StreamState) => {
      // When the underlying stream stops or errors out, the subscription dries
      // up but ffmpeg would otherwise linger as an orphan process. Tear it down
      // so each device's ffmpeg lifetime is bounded by its service.
      if (state === StreamState.STOPPED || state === StreamState.ERROR) {
        this.stop();
      }
    };
  }

  start(): void {
    if (!this.enabled || this.running) return;
    const videoCodec = this.service.currentMeta?.videoCodec;
    if (videoCodec && videoCodec !== VideoCodec.H264) {
      throw new Error(
        `snapshot cache only supports h264 video, got ${videoCodec}`,
      );
    }

    this.running = true;
    this.staleEmitted = false;
    this.bindServiceState();
    this.process = this.spawnProcess(this.ffmpegPath, this.buildFfmpegArgs());
    this.process.stdout.on("data", (chunk: Buffer) => this.handleStdout(chunk));
    this.process.stderr.on("data", (chunk: Buffer) => {
      this.handleStderr(chunk);
    });
    this.process.once("error", (e: Error) => this.handleProcessError(e));
    this.process.once(
      "exit",
      (code: number | null, signal: NodeJS.Signals | null) => {
        if (this.running) {
          this.cleanup({ clearSnapshot: true, killProcess: false });
          this.emit(
            "error",
            new Error(
              `ffmpeg exited unexpectedly: code=${code} signal=${signal}${this.formatStderrTail()}`,
            ),
          );
        }
      },
    );

    this.subscription = this.service.subscribe();
    this.startStaleWatchdog();
    void this.pumpVideoPackets();
  }

  stop(): void {
    this.cleanup({ clearSnapshot: false, killProcess: true });
  }

  latest(): Snapshot | null {
    return this.currentSnapshot;
  }

  /**
   * Resolve with a snapshot, waiting up to `timeoutMs` for the first frame if
   * the cache has not produced one yet. Lets HTTP handlers avoid both a busy
   * 404-poll loop and an unbounded hang while ffmpeg warms up.
   */
  waitForFresh(timeoutMs = 3000): Promise<Snapshot> {
    if (this.currentSnapshot) return Promise.resolve(this.currentSnapshot);
    if (!this.running || !this.enabled) {
      return Promise.reject(new Error("snapshot cache is not running"));
    }
    return new Promise<Snapshot>((resolve, reject) => {
      const onShot = (shot: Snapshot) => {
        cleanup();
        resolve(shot);
      };
      const onError = (e: Error) => {
        cleanup();
        reject(e);
      };
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error(`snapshot not available within ${timeoutMs}ms`));
      }, timeoutMs);
      timer.unref?.();
      const cleanup = () => {
        clearTimeout(timer);
        this.removeListener("snapshot", onShot);
        this.removeListener("error", onError);
      };
      this.once("snapshot", onShot);
      this.once("error", onError);
    });
  }

  private buildFfmpegArgs(): string[] {
    return [
      "-hide_banner",
      "-loglevel",
      "error",
      "-f",
      "h264",
      "-i",
      "pipe:0",
      "-vf",
      [
        "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709",
        `fps=${this.fps}`,
        "format=yuvj420p",
      ].join(","),
      "-q:v",
      String(jpegQualityToMjpegQscale(this.quality)),
      "-f",
      "image2pipe",
      "-vcodec",
      "mjpeg",
      "pipe:1",
    ];
  }

  private async pumpVideoPackets(): Promise<void> {
    const subscription = this.subscription;
    const proc = this.process;
    if (!subscription || !proc) return;

    try {
      for await (const packet of subscription) {
        if (!this.running) break;
        if (
          packet.kind !== MediaKind.VIDEO ||
          packet.payload.byteLength === 0
        ) {
          continue;
        }
        await this.writePacket(proc, packet);
      }
    } catch (e) {
      this.handlePumpError(e);
    }
  }

  private async writePacket(
    proc: FfmpegProcess,
    packet: MediaPacket,
  ): Promise<void> {
    if (proc.stdin.write(packet.payload)) return;
    await this.waitForDrain(proc);
  }

  private waitForDrain(proc: FfmpegProcess): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const onDrain = () => {
        cleanup();
        resolve();
      };
      const onError = (e: Error) => {
        cleanup();
        reject(e);
      };
      const timer = setTimeout(() => {
        cleanup();
        reject(
          new Error(
            `ffmpeg stdin did not drain within ${this.drainTimeoutMs}ms`,
          ),
        );
      }, this.drainTimeoutMs);
      timer.unref?.();
      const cleanup = () => {
        clearTimeout(timer);
        proc.stdin.removeListener("drain", onDrain);
        proc.stdin.removeListener("error", onError);
      };
      proc.stdin.once("drain", onDrain);
      proc.stdin.once("error", onError);
    });
  }

  private handleStdout(chunk: Buffer<ArrayBufferLike>): void {
    this.stdoutBuffer = Buffer.concat([this.stdoutBuffer, chunk]);

    // Guard against a corrupt/headerless ffmpeg stream where no EOI ever
    // arrives: without a cap the remainder would grow without bound.
    if (this.stdoutBuffer.byteLength > this.maxStdoutBytes) {
      this.stdoutBuffer = this.stdoutBuffer.subarray(-this.maxStdoutBytes);
    }

    const result = extractJpegFrames(this.stdoutBuffer);
    this.stdoutBuffer = result.remainder;

    for (const frame of result.frames) {
      this.currentSnapshot = {
        contentType: "image/jpeg",
        data: frame,
        timestampMs: Date.now(),
      };
      this.staleEmitted = false;
      this.emit("snapshot", this.currentSnapshot);
    }
  }

  private handleStderr(chunk: Buffer<ArrayBufferLike>): void {
    const text = chunk.toString("utf8");
    this.stderrTail = (this.stderrTail + text).slice(-4000);
    this.emit("ffmpegLog", text);
  }

  private handleProcessError(e: Error): void {
    if (!this.running) return;
    this.cleanup({ clearSnapshot: true, killProcess: false });
    this.emit("error", e);
  }

  private handlePumpError(e: unknown): void {
    if (!this.running) return;
    // A pump failure (e.g. stdin stalled past the drain timeout) means ffmpeg
    // is wedged; kill it so the cache can be restarted cleanly.
    this.cleanup({ clearSnapshot: true, killProcess: true });
    this.emit("error", e);
  }

  private startStaleWatchdog(): void {
    if (this.staleTimeoutMs <= 0) return;
    const timer = setInterval(() => {
      if (!this.running || !this.currentSnapshot || this.staleEmitted) return;
      const age = Date.now() - this.currentSnapshot.timestampMs;
      if (age > this.staleTimeoutMs) {
        this.staleEmitted = true;
        this.emit("stale", { ageMs: age, snapshot: this.currentSnapshot });
      }
    }, STALE_CHECK_INTERVAL_MS);
    timer.unref?.();
    this.staleTimer = timer;
  }

  private bindServiceState(): void {
    const emitter = this.service as unknown as {
      on?: (event: string, listener: (...args: any[]) => void) => void;
    };
    emitter.on?.("state", this.onServiceState);
  }

  private unbindServiceState(): void {
    const emitter = this.service as unknown as {
      removeListener?: (
        event: string,
        listener: (...args: any[]) => void,
      ) => void;
    };
    emitter.removeListener?.("state", this.onServiceState);
  }

  private cleanup(options: {
    clearSnapshot: boolean;
    killProcess: boolean;
  }): void {
    this.running = false;
    this.unbindServiceState();

    if (this.staleTimer) {
      clearInterval(this.staleTimer);
      this.staleTimer = null;
    }

    this.subscription?.return?.();
    this.subscription = null;

    const proc = this.process;
    this.process = null;
    if (proc) {
      proc.stdin.end();
      if (options.killProcess) this.killProcess(proc);
    }

    this.stdoutBuffer = Buffer.alloc(0);
    this.stderrTail = "";
    if (options.clearSnapshot) this.currentSnapshot = null;
  }

  private killProcess(proc: FfmpegProcess): void {
    proc.kill();
    // Escalate to SIGKILL if ffmpeg ignores the polite signal.
    const timer = setTimeout(() => {
      try {
        proc.kill("SIGKILL");
      } catch {
        // process already gone
      }
    }, this.killTimeoutMs);
    timer.unref?.();
    proc.once("exit", () => clearTimeout(timer));
  }

  private formatStderrTail(): string {
    const tail = this.stderrTail.trim();
    return tail ? ` stderr=${tail}` : "";
  }
}

function normalizeFps(value: number): number {
  if (!Number.isFinite(value) || value <= 0 || value > 60) {
    throw new Error(`snapshot fps must be a finite number between 0 and 60`);
  }
  return value;
}

function normalizeJpegQuality(value: number): number {
  if (!Number.isFinite(value) || value < 1 || value > 100) {
    throw new Error(
      `snapshot JPEG quality must be a finite number from 1 to 100`,
    );
  }
  return Math.round(value);
}

function jpegQualityToMjpegQscale(quality: number): number {
  return Math.max(2, Math.min(31, Math.round(31 - (quality / 100) * 29)));
}
