import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import {
  FfmpegSnapshotCache,
  ScrcpyStreamService,
} from "android-stream-scrcpy-v4";
import { ScrcpyWebSocketBridge } from "android-stream-scrcpy-v4/websocket";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
};

function envFlag(name: string): boolean {
  return ["1", "true", "yes", "on"].includes(
    (process.env[name] || "").toLowerCase(),
  );
}

function envNumber(name: string, fallback: number): number {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function screenshotDownloadFilename(): string {
  const timestamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d{3}Z$/, "Z");
  return `screenshot-${timestamp}.jpg`;
}

function screenshotHeaders(extra: Record<string, string> = {}) {
  return {
    "access-control-allow-origin": "*",
    "access-control-expose-headers": "content-disposition",
    "cache-control": "no-store",
    ...extra,
  };
}

async function main() {
  const deviceSerial =
    process.env.ANDROID_SERIAL || process.env.ADB_SERIAL || undefined;
  const service = new ScrcpyStreamService({
    deviceSerial,
    maxSize: 720,
    video: true,
    audio: true,
    control: true,
  });
  const snapshotCache = envFlag("SNAPSHOT_ENABLED")
    ? new FfmpegSnapshotCache(service, {
        enabled: true,
        fps: envNumber("SNAPSHOT_FPS", 2),
        quality: envNumber("SNAPSHOT_JPEG_QUALITY", 85),
        ffmpegPath: process.env.FFMPEG_PATH || "ffmpeg",
      })
    : null;

  const webDemoPath = path.resolve(__dirname, "../../web_demo");
  const server = createServer(async (req, res) => {
    const url = new URL(req.url || "/", "http://localhost");
    if (url.pathname === "/favicon.ico") {
      res.writeHead(204);
      res.end();
      return;
    }

    if (url.pathname === "/screenshot.jpg") {
      if (!snapshotCache) {
        res.writeHead(
          404,
          screenshotHeaders({ "content-type": "text/plain; charset=utf-8" }),
        );
        res.end("snapshot cache is disabled");
        return;
      }

      const snapshot = snapshotCache.latest();
      if (!snapshot) {
        res.writeHead(
          404,
          screenshotHeaders({ "content-type": "text/plain; charset=utf-8" }),
        );
        res.end("no screenshot available yet");
        return;
      }

      res.writeHead(
        200,
        screenshotHeaders({
          "content-type": snapshot.contentType,
          "content-length": String(snapshot.data.byteLength),
          "content-disposition": `attachment; filename="${screenshotDownloadFilename()}"`,
        }),
      );
      res.end(snapshot.data);
      return;
    }

    const pathname = url.pathname === "/" ? "/scrcpy.html" : url.pathname;
    const candidate = path.normalize(path.join(webDemoPath, pathname));

    if (!candidate.startsWith(webDemoPath)) {
      res.writeHead(403);
      res.end("Forbidden");
      return;
    }

    try {
      const file = await stat(candidate);
      if (!file.isFile()) throw new Error("not a file");
      res.writeHead(200, {
        "content-type":
          MIME_TYPES[path.extname(candidate)] || "application/octet-stream",
      });
      createReadStream(candidate).pipe(res);
    } catch {
      res.writeHead(404);
      res.end("Not found");
    }
  });

  const bridge = new ScrcpyWebSocketBridge(service, { server });

  service.on("state", (state) => {
    console.log(`[service] State: ${state}`);
  });

  service.on("error", (err) => {
    console.error(`[service] Error: ${err.message}`);
  });
  snapshotCache?.on("error", (err) => {
    console.error(`[snapshot] Error: ${err.message}`);
  });
  snapshotCache?.on("ffmpegLog", (message) => {
    for (const line of message.trim().split(/\r?\n/)) {
      if (line) console.error(`[snapshot:ffmpeg] ${line}`);
    }
  });

  try {
    if (deviceSerial) {
      console.log(`Using requested ADB device: ${deviceSerial}`);
    }
    console.log("Starting scrcpy service...");
    await service.start();
    snapshotCache?.start();

    const port = 8000;
    server.listen(port, () => {
      console.log(`Web demo available at http://localhost:${port}`);
      console.log(
        `WebSocket bridge active at ws://localhost:${port}/ws/scrcpy`,
      );
      if (snapshotCache) {
        console.log(
          `Snapshot endpoint active at http://localhost:${port}/screenshot.jpg`,
        );
      }
    });
  } catch (e) {
    snapshotCache?.stop();
    bridge.close();
    console.error("Failed to start server", e);
    process.exitCode = 1;
  }

  const shutdown = () => {
    snapshotCache?.stop();
    bridge.close();
    service.stop();
    server.close();
  };
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
}

main();
