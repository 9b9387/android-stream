import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { ScrcpyStreamService } from "android-stream-scrcpy-v4";
import { ScrcpyWebSocketBridge } from "android-stream-scrcpy-v4/websocket";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
};

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

  const webDemoPath = path.resolve(__dirname, "../../web_demo");
  const server = createServer(async (req, res) => {
    const url = new URL(req.url || "/", "http://localhost");
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

  try {
    if (deviceSerial) {
      console.log(`Using requested ADB device: ${deviceSerial}`);
    }
    console.log("Starting scrcpy service...");
    await service.start();

    const port = 8000;
    server.listen(port, () => {
      console.log(`Web demo available at http://localhost:${port}`);
      console.log(
        `WebSocket bridge active at ws://localhost:${port}/ws/scrcpy`,
      );
    });
  } catch (e) {
    bridge.close();
    console.error("Failed to start server", e);
    process.exitCode = 1;
  }
}

main();
