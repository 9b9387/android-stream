# @9b9387/android-stream-scrcpy

TypeScript scrcpy client library for Node.js and Electron. It starts `scrcpy-server` through `@devicefarmer/adbkit`, reads the video/audio/control sockets, and exposes media packets as Web Standards binary types (`Uint8Array`).

The bundled protocol implementation targets scrcpy `4.0`. Protocol code is versioned so future versions, such as `4.1`, can be added side by side without rewriting the backend or service layers.

> Runtime note: the root package is Node-only. Use it from a Node.js process, an Electron main process, a Next.js Node runtime route/server, or another long-lived backend process. Do not import the root package from browser code, Next.js Client Components, or Edge Runtime handlers.

## Project Structure

```text
node-lib/
  assets/
    scrcpy-server-v4.0.jar
  examples/
    demo.ts
    server.ts
  src/
    backend/
      adb/
        scrcpy-adb-client.ts
      io/
        buffered-stream-reader.ts
      server/
        options.ts
        server-command.ts
        socket-name.ts
      scrcpy-backend.ts
      index.ts
    protocol/
      core/
        binary.ts
        types.ts
      registry.ts
      v4_0/
        codecs.ts
        control-message.ts
        control-message-types.ts
        device-message.ts
        frame-header.ts
        server-options.ts
        string-payload.ts
        index.ts
      index.ts
    service/
      packet-queue-subscriber.ts
      scrcpy-stream-service.ts
      types.ts
      index.ts
    snapshot/
      ffmpeg-snapshot-cache.ts
      types.ts
      index.ts
    websocket/
      binary-packet.ts
      control-json.ts
      scrcpy-websocket-bridge.ts
      types.ts
      index.ts
    index.ts
```

### Module Responsibilities

- `src/protocol/`: scrcpy wire protocol implementation. `core/` contains shared binary helpers and protocol interfaces; `registry.ts` selects a protocol adapter by version; `v4_0/` contains scrcpy 4.0 frame, control, device, codec, and server-option logic.
- `src/backend/`: adbkit-only device integration. `adb/` wraps adbkit operations, `io/` contains socket reading utilities, `server/` builds and normalizes scrcpy server launch details, and `scrcpy-backend.ts` orchestrates the streaming connection.
- `src/service/`: public stream service. It manages state, caches decoder config/session snapshots, and exposes `for await...of` media packet subscriptions.
- `src/snapshot/`: optional Node-side screenshot cache. It pipes H.264 packets into ffmpeg and stores the latest JPEG frame.
- `src/websocket/`: optional `ws` bridge for browser clients. It is exported from the `./websocket` subpath and is not part of the root API.
- `examples/`: runnable examples kept outside the library build.

## Install

Install the package in your application:

```bash
npm install @9b9387/android-stream-scrcpy
```

Core runtime dependency is `@devicefarmer/adbkit`. The library does not call the `adb` system command directly.

If your application uses the optional WebSocket bridge, install `ws` in the application:

```bash
npm install ws
```

If your application uses the optional screenshot cache, make sure `ffmpeg` is available on `PATH`, or pass `ffmpegPath` to `FfmpegSnapshotCache`.

## Build From Source

```bash
cd node-lib
npm install
npm run build
```

## Package Entrypoints

- `@9b9387/android-stream-scrcpy`: Node-only service/backend API.
- `@9b9387/android-stream-scrcpy/protocol`: protocol adapters and types. This is the safest entrypoint for code that only needs scrcpy protocol constants, codecs, and message types.
- `@9b9387/android-stream-scrcpy/websocket`: optional Node-only `ws` bridge.
- `@9b9387/android-stream-scrcpy/snapshot`: optional Node-only ffmpeg screenshot cache.

The package is ESM-only and requires Node.js 20 or newer.

## Run Examples

Run the console stream demo:

```bash
npm run example:demo
```

Run the browser demo server:

```bash
npm run example:server
```

The server serves the existing demo page from the repository root:

```text
../web_demo/scrcpy.html
```

Then open:

```text
http://localhost:8000
```

You can also request the page directly:

```text
http://localhost:8000/scrcpy.html
```

Choose a specific connected Android device by serial:

```bash
ANDROID_SERIAL=<device-serial> npm run example:server
```

`ADB_SERIAL` is also supported for compatibility with existing local workflows.

Enable the optional Node-side screenshot cache for the browser demo:

```bash
SNAPSHOT_ENABLED=1 SNAPSHOT_FPS=2 SNAPSHOT_JPEG_QUALITY=85 npm run example:server
```

This requires `ffmpeg` on `PATH` by default. Set `FFMPEG_PATH=/path/to/ffmpeg`
if the binary lives elsewhere. When enabled, the demo exposes:

```text
http://localhost:8000/screenshot.jpg
```

The `scrcpy.html` page also shows a `截图` button that downloads the latest
cached JPEG. The cache is produced from the scrcpy H.264 stream in Node; it does
not call ADB `screencap` and does not require a browser canvas.

## Integrate The Core Library

```typescript
import { ScrcpyStreamService } from "@9b9387/android-stream-scrcpy";

const service = new ScrcpyStreamService({
  protocolVersion: "4.0",
  maxSize: 1080,
  video: true,
  audio: true,
  control: true,
});

const meta = await service.start();
console.log(`Connected to ${meta.deviceName}`);

for await (const packet of service.subscribe()) {
  // packet.kind: "video" | "audio" | "session"
  // packet.payload: Uint8Array
  if (packet.kind === "video") {
    // Feed H.264/H.265/AV1 bytes into your decoder.
  }
}
```

Stop streaming when your application shuts down:

```typescript
service.stop();
```

## Send Control Messages

```typescript
import {
  ControlMessageType,
  KEY_ACTION_DOWN,
  ScrcpyStreamService,
} from "@9b9387/android-stream-scrcpy";

const service = new ScrcpyStreamService();
await service.start();

service.sendControlMessage({
  type: ControlMessageType.INJECT_KEYCODE,
  action: KEY_ACTION_DOWN,
  keycode: 26,
});
```

The scrcpy 4.0 adapter includes key, text, touch, scroll, clipboard, UHID, app start, display power, camera, and display resize control messages.

## Integrate The WebSocket Bridge

The WebSocket bridge is optional and imported from a subpath:

```typescript
import { createServer } from "node:http";
import { ScrcpyStreamService } from "@9b9387/android-stream-scrcpy";
import { ScrcpyWebSocketBridge } from "@9b9387/android-stream-scrcpy/websocket";

const service = new ScrcpyStreamService({ protocolVersion: "4.0" });
const server = createServer();
const bridge = new ScrcpyWebSocketBridge(service, { server });

await service.start();
server.listen(8000);

process.on("SIGINT", () => {
  bridge.close();
  service.stop();
  server.close();
});
```

The bridge sends an initial JSON `init` message, then binary media packets with a 16-byte header followed by the media payload.

## Integrate The Screenshot Cache

The screenshot cache is optional and imported from the `./snapshot` subpath:

```typescript
import { ScrcpyStreamService } from "@9b9387/android-stream-scrcpy";
import { FfmpegSnapshotCache } from "@9b9387/android-stream-scrcpy/snapshot";

const service = new ScrcpyStreamService({ videoCodec: "h264" });
const snapshots = new FfmpegSnapshotCache(service, {
  enabled: true,
  fps: 2,
  quality: 85,
});

await service.start();
snapshots.start();

const latest = snapshots.latest();
if (latest) {
  // latest.contentType === "image/jpeg"
  // latest.data is a Buffer containing JPEG bytes.
}
```

The first implementation supports H.264 input and JPEG output. The configured
`fps` limits how often ffmpeg emits JPEG frames; ffmpeg still receives the
continuous H.264 stream so inter-frame decoding remains correct.

## Next.js And Electron Usage

### Next.js

Use this package only from the server side of a self-hosted or long-lived Node.js runtime:

```typescript
export const runtime = "nodejs";

import { ScrcpyStreamService } from "@9b9387/android-stream-scrcpy";
```

Do not import the root package from Client Components, browser bundles, Middleware,
or Edge Runtime route handlers. The backend opens ADB sockets and long-lived media
streams, so a custom Node.js server or separate local daemon is usually a better
fit than a short-lived serverless function.

### Electron

Create and manage `ScrcpyStreamService` in the main process. Renderer processes
should communicate with the main process through IPC or connect to the optional
WebSocket bridge.

When packaging Electron apps, make sure `assets/scrcpy-server-v4.0.jar` is copied
as a runtime resource. If your packager moves or packs assets into an archive,
pass an explicit `serverJarPath` to `ScrcpyStreamService`. If screenshot caching
is enabled, also ship `ffmpeg` yourself or pass `ffmpegPath`.

## Protocol Versioning

`protocolVersion` defaults to `"4.0"`:

```typescript
const service = new ScrcpyStreamService({ protocolVersion: "4.0" });
```

To add a future scrcpy version:

1. Add a new adapter under `src/protocol/`.
2. Register it in `src/protocol/registry.ts`.
3. Add the matching `scrcpy-server` jar to `assets/`.
4. Add fixed-byte protocol tests for changed behavior.

The backend depends on the protocol interface, so frame parsing, control serialization, server options, and socket naming can evolve per version.

## Development Commands

```bash
npm run format
npm run lint
npm run typecheck
npm test
npm run build
npm pack --dry-run
```

End-to-end streaming requires a connected Android device. Unit tests cover protocol serialization/parsing, backend option normalization, subscriber queue behavior, and WebSocket packet encoding.

## Publish

Before publishing, make sure you are logged in to the npm account that owns the
`@9b9387` scope:

```bash
npm whoami
```

Run the release checks and inspect the tarball contents:

```bash
npm run typecheck
npm run test
npm run lint
npm run build
npm pack --dry-run
```

Publish the public scoped package:

```bash
npm publish --access public
```
