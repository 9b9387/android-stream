import express from "express";
import { createServer } from "node:http";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { ScrcpyV4Service, ScrcpyV4WebBridge } from "./index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

async function main() {
  const service = new ScrcpyV4Service({
    maxSize: 720,
    video: true,
    audio: true,
    control: true,
  });

  const app = express();
  const server = createServer(app);

  // Serve static files from the project's web_demo directory
  const webDemoPath = path.resolve(__dirname, "../../web_demo");
  app.use(express.static(webDemoPath));

  // Initialize the bridge
  const bridge = new ScrcpyV4WebBridge(service, { server });

  service.on("state", (state) => {
    console.log(`[service] State: ${state}`);
  });

  service.on("error", (err) => {
    console.error(`[service] Error: ${err.message}`);
  });

  try {
    console.log("Starting scrcpy service...");
    await service.start();
    
    const port = 8000;
    server.listen(port, () => {
      console.log(`Web demo available at http://localhost:${port}`);
      console.log(`WebSocket bridge active at ws://localhost:${port}/ws/scrcpy`);
    });
  } catch (e) {
    console.error("Failed to start server", e);
    process.exit(1);
  }
}

main();
