import { EventEmitter } from "node:events";
import { once } from "node:events";
import { PassThrough } from "node:stream";
import { describe, expect, test } from "vitest";
import {
  extractJpegFrames,
  FfmpegSnapshotCache,
} from "../src/snapshot/ffmpeg-snapshot-cache.js";

describe("extractJpegFrames", () => {
  test("extracts complete JPEG frames across arbitrary chunks", () => {
    let state = Buffer.alloc(0);
    const first = Buffer.from([0xff, 0xd8, 0x01, 0x02, 0xff, 0xd9]);
    const second = Buffer.from([0xff, 0xd8, 0x03, 0xff, 0xd9]);

    let result = extractJpegFrames(
      Buffer.concat([state, first.subarray(0, 3)]),
    );
    expect(result.frames).toEqual([]);
    state = result.remainder;

    result = extractJpegFrames(
      Buffer.concat([state, first.subarray(3), second, Buffer.from([0xff])]),
    );

    expect(result.frames).toEqual([first, second]);
    expect(result.remainder).toEqual(Buffer.from([0xff]));
  });
});

describe("FfmpegSnapshotCache", () => {
  test("does not subscribe or spawn when disabled", () => {
    let subscribed = false;
    const service = {
      subscribe() {
        subscribed = true;
        throw new Error("should not subscribe");
      },
    };

    const cache = new FfmpegSnapshotCache(service as any, {
      enabled: false,
      spawnProcess: () => {
        throw new Error("should not spawn");
      },
    });

    cache.start();

    expect(subscribed).toBe(false);
    expect(cache.latest()).toBeNull();
  });

  test("spawns ffmpeg with h264 input and mjpeg output", () => {
    const service = {
      subscribe: async function* () {},
    };
    const spawned: { command: string; args: string[] }[] = [];

    const cache = new FfmpegSnapshotCache(service as any, {
      enabled: true,
      fps: 2,
      quality: 90,
      ffmpegPath: "custom-ffmpeg",
      spawnProcess: (command, args) => {
        spawned.push({ command, args });
        return fakeProcess();
      },
    });

    cache.start();
    cache.stop();

    expect(spawned).toEqual([
      {
        command: "custom-ffmpeg",
        args: [
          "-hide_banner",
          "-loglevel",
          "error",
          "-f",
          "h264",
          "-i",
          "pipe:0",
          "-vf",
          "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709,fps=2,format=yuvj420p",
          "-q:v",
          "5",
          "-f",
          "image2pipe",
          "-vcodec",
          "mjpeg",
          "pipe:1",
        ],
      },
    ]);
  });

  test("updates latest snapshot from ffmpeg stdout", async () => {
    const service = {
      subscribe: async function* () {},
    };
    const proc = fakeProcess();
    const cache = new FfmpegSnapshotCache(service as any, {
      enabled: true,
      spawnProcess: () => proc,
    });

    cache.start();
    const jpeg = Buffer.from([0xff, 0xd8, 0x10, 0x20, 0xff, 0xd9]);
    proc.stdout.write(jpeg);
    await Promise.resolve();

    expect(cache.latest()?.contentType).toBe("image/jpeg");
    expect(cache.latest()?.data).toEqual(jpeg);
    cache.stop();
  });

  test("writes video payloads into ffmpeg stdin", async () => {
    const payload = new Uint8Array([0, 0, 0, 1, 0x65]);
    const service = {
      subscribe: async function* () {
        yield {
          kind: "video",
          ptsUs: 1n,
          config: false,
          keyFrame: true,
          payload,
        };
      },
    };
    const proc = fakeProcess();
    const cache = new FfmpegSnapshotCache(service as any, {
      enabled: true,
      spawnProcess: () => proc,
    });

    cache.start();
    const [written] = await once(proc.stdin, "data");

    expect(written).toEqual(Buffer.from(payload));
    cache.stop();
  });

  test("cleans up after ffmpeg exits so cache can be restarted", () => {
    const service = {
      subscribe: async function* () {},
    };
    const first = fakeProcess();
    const second = fakeProcess();
    const processes = [first, second];
    const spawned: ReturnType<typeof fakeProcess>[] = [];
    const cache = new FfmpegSnapshotCache(service as any, {
      enabled: true,
      spawnProcess: () => {
        const proc = processes.shift()!;
        spawned.push(proc);
        return proc;
      },
    });
    cache.on("error", () => {});

    cache.start();
    first.emit("exit", 1, null);
    cache.start();

    expect(spawned).toEqual([first, second]);
    cache.stop();
  });

  test("rejects invalid fps and quality options", () => {
    const service = {
      subscribe: async function* () {},
    };

    expect(() => new FfmpegSnapshotCache(service as any, { fps: 0 })).toThrow(
      /fps/,
    );
    expect(
      () => new FfmpegSnapshotCache(service as any, { quality: 0 }),
    ).toThrow(/quality/);
    expect(
      () => new FfmpegSnapshotCache(service as any, { quality: 101 }),
    ).toThrow(/quality/);
  });
});

function fakeProcess() {
  const emitter = new EventEmitter() as EventEmitter & {
    stdin: PassThrough;
    stdout: PassThrough;
    stderr: PassThrough;
    kill: () => boolean;
  };
  emitter.stdin = new PassThrough();
  emitter.stdout = new PassThrough();
  emitter.stderr = new PassThrough();
  emitter.kill = () => true;
  return emitter;
}
