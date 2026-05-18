import { ScrcpyStreamService, StreamState } from "android-stream-scrcpy-v4";

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

  service.on("state", (state: StreamState) => {
    console.log(`State: ${state}`);
  });

  service.on("error", (err) => {
    console.error("Service error:", err);
  });

  try {
    if (deviceSerial) {
      console.log(`Using requested ADB device: ${deviceSerial}`);
    }
    const meta = await service.start();
    console.log("Connected to device!");
    console.log(`Device: ${meta.deviceName}`);
    console.log(`Video: ${meta.videoCodec} ${meta.width}x${meta.height}`);
    console.log(`Audio: ${meta.audioCodec}`);

    let videoPackets = 0;
    let audioPackets = 0;

    for await (const packet of service.subscribe()) {
      if (packet.kind === "video") videoPackets++;
      if (packet.kind === "audio") audioPackets++;

      if ((videoPackets + audioPackets) % 100 === 0) {
        console.log(`Packets: video=${videoPackets}, audio=${audioPackets}`);
      }
    }
  } catch (e) {
    console.error("Failed to run demo", e);
    process.exitCode = 1;
  } finally {
    service.stop();
  }
}

main();
