import { ScrcpyV4Service, StreamState } from "./index.js";

async function main() {
  const service = new ScrcpyV4Service({
    maxSize: 720,
    video: true,
    audio: true,
    control: true,
  });

  service.on("state", (state) => {
    console.log(`State changed: ${state}`);
  });

  (service as any).backend.on("serverLog", (log: string) => {
    console.log(`[server] ${log.trim()}`);
  });

  service.on("error", (err) => {
    console.error(`Error: ${err.message}`);
  });

  try {
    const meta = await service.start();
    console.log("Connected to device!");
    console.log(`Device: ${meta.deviceName}`);
    console.log(`Resolution: ${meta.width}x${meta.height}`);
    console.log(`Codecs: Video=${meta.videoCodec}, Audio=${meta.audioCodec}`);

    const subscription = service.subscribe();
    console.log("Subscribed to stream, waiting for packets...");

    let count = 0;
    for await (const packet of subscription) {
      count++;
      if (count % 100 === 0) {
        console.log(`Received ${count} packets... (latest kind: ${packet.kind}, size: ${packet.payload.length})`);
      }
      if (count >= 500) {
        console.log("Reached 500 packets, stopping demo.");
        break;
      }
    }

    service.stop();
    process.exit(0);
  } catch (e) {
    console.error("Failed to start service", e);
    process.exit(1);
  }
}

main();
