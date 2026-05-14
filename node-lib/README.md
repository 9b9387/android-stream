# Android Stream - Scrcpy V4 Node.js Library

A TypeScript implementation of the Scrcpy V4.0 protocol for Node.js and Electron applications.

## Features
- **Pure TypeScript**: No native dependencies other than ADB.
- **Scrcpy V4.0 Support**: Implements the latest wire protocol, including session packets and audio support.
- **ADB Integration**: Uses `@devicefarmer/adbkit` for robust device management.
- **Async Iterators**: Stream media packets using standard `for await...of` loops.
- **WebSocket Bridge**: Built-in support for streaming to browser-based clients.

## Installation

```bash
cd node-lib
npm install
npm run build
```

## Architecture

- **`protocol.ts`**: Low-level packet parsing and control message serialization.
- **`backend.ts`**: Manages `adb` server deployment and socket connections.
- **`service.ts`**: Orchestrates the backend and provides high-level stream subscriptions.
- **`web.ts`**: Optional WebSocket bridge for remote clients.

## Usage

### Basic Stream Consumption

```typescript
import { ScrcpyV4Service } from './dist/index.js';

const service = new ScrcpyV4Service({
  maxSize: 1080,
  video: true,
  audio: true,
});

const meta = await service.start();
console.log(`Connected to ${meta.deviceName}`);

const subscription = service.subscribe();
for await (const packet of subscription) {
  // packet.kind: 'video' | 'audio' | 'session'
  // packet.payload: Buffer (H.264/Opus data)
}
```

### Sending Control Messages

```typescript
service.sendControlMessage({
  type: ControlMessageType.INJECT_KEYCODE,
  action: KEY_ACTION_DOWN,
  keycode: 26, // Power button
});
```

## Demo
Run the included demo to verify connectivity:
```bash
npm run build
node dist/demo.js
```
