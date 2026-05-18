import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { once } from "node:events";
import {
  MediaKind,
  MediaPacket,
  ScrcpyStreamService,
} from "../service/index.js";
import { VideoCodec } from "../protocol/index.js";
import { FfmpegProcess, Snapshot, SnapshotCacheOptions } from "./types.js";

const JPEG_SOI = Buffer.from([0xff, 0xd8]);
const JPEG_EOI = Buffer.from([0xff, 0xd9]);
const DEFAULT_SNAPSHOT_FPS = 2;
const DEFAULT_JPEG_QUALITY = 85;

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
    this.spawnProcess =
      options.spawnProcess ??
      ((command, args) =>
        spawn(command, args, {
          stdio: ["pipe", "pipe", "pipe"],
        }));
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
    void this.pumpVideoPackets();
  }

  stop(): void {
    this.cleanup({ clearSnapshot: false, killProcess: true });
  }

  latest(): Snapshot | null {
    return this.currentSnapshot;
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
      if (this.running) this.emit("error", e);
    }
  }

  private async writePacket(
    proc: FfmpegProcess,
    packet: MediaPacket,
  ): Promise<void> {
    if (proc.stdin.write(packet.payload)) return;
    await once(proc.stdin, "drain");
  }

  private handleStdout(chunk: Buffer<ArrayBufferLike>): void {
    this.stdoutBuffer = Buffer.concat([this.stdoutBuffer, chunk]);
    const result = extractJpegFrames(this.stdoutBuffer);
    this.stdoutBuffer = result.remainder;

    for (const frame of result.frames) {
      this.currentSnapshot = {
        contentType: "image/jpeg",
        data: frame,
        timestampMs: Date.now(),
      };
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

  private cleanup(options: {
    clearSnapshot: boolean;
    killProcess: boolean;
  }): void {
    this.running = false;
    this.subscription?.return?.();
    this.subscription = null;

    const proc = this.process;
    this.process = null;
    if (proc) {
      proc.stdin.end();
      if (options.killProcess) proc.kill();
    }

    this.stdoutBuffer = Buffer.alloc(0);
    this.stderrTail = "";
    if (options.clearSnapshot) this.currentSnapshot = null;
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
