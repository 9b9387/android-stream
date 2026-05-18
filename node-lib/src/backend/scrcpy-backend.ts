import { EventEmitter } from "node:events";
import * as net from "node:net";
import {
  AUDIO_CODEC_IDS,
  AudioCodec,
  ControlMessage,
  DeviceMessage,
  FrameMeta,
  SessionPacket,
  VIDEO_CODEC_IDS,
  VideoCodec,
} from "../protocol/index.js";
import {
  dataView,
  utf8Decode,
  zeroTerminatedUtf8Decode,
} from "../protocol/core/binary.js";
import { ScrcpyAdbClient } from "./adb/scrcpy-adb-client.js";
import { BufferedStreamReader } from "./io/buffered-stream-reader.js";
import {
  NormalizedScrcpyBackendOptions,
  ScrcpyBackendOptions,
  normalizeBackendOptions,
} from "./server/options.js";
import {
  buildServerCommand,
  getDeviceServerPath,
} from "./server/server-command.js";

export interface StreamMeta {
  deviceName: string;
  videoCodec?: VideoCodec;
  audioCodec?: AudioCodec;
  width: number;
  height: number;
}

type SocketKind = "video" | "audio" | "control";

export class ScrcpyBackend extends EventEmitter {
  private readonly options: NormalizedScrcpyBackendOptions;
  private readonly adbClient: ScrcpyAdbClient;
  private device: any = null;
  private serverStream: any = null;
  private videoSocket: net.Socket | null = null;
  private audioSocket: net.Socket | null = null;
  private controlSocket: net.Socket | null = null;
  private videoReader: BufferedStreamReader | null = null;
  private audioReader: BufferedStreamReader | null = null;
  private controlReader: BufferedStreamReader | null = null;
  private running = false;
  private meta: StreamMeta | null = null;

  constructor(
    options: ScrcpyBackendOptions = {},
    adbClient = new ScrcpyAdbClient(),
  ) {
    super();
    this.options = normalizeBackendOptions(options);
    this.adbClient = adbClient;
  }

  async start(): Promise<StreamMeta> {
    if (this.running) throw new Error("backend already running");

    const selected = await this.adbClient.selectDevice(
      this.options.deviceSerial,
    );
    this.device = selected.device;

    await this.spawnServer();
    await this.connectSockets();
    this.meta = await this.handshake();
    this.running = true;

    this.startWorkerLoops();
    return this.meta;
  }

  stop(): void {
    this.running = false;
    this.cleanup();
  }

  sendControlMessage(msg: ControlMessage): void {
    if (!this.running || !this.controlSocket) {
      throw new Error("control channel is not available");
    }
    this.controlSocket.write(
      this.options.protocol.serializeControlMessage(msg),
    );
  }

  private async spawnServer(): Promise<void> {
    const devicePath = getDeviceServerPath(this.options.protocol.serverVersion);
    await this.adbClient.pushServerJar(
      this.device,
      this.options.serverJarPath,
      devicePath,
      this.options.pushTimeoutMs,
    );

    const command = buildServerCommand(devicePath, this.options);
    this.serverStream = await this.adbClient.startServer(this.device, command);
    this.serverStream.on("data", (chunk: Uint8Array) => {
      this.emit("serverLog", utf8Decode(chunk));
    });

    await this.waitForServerStartProbe();
  }

  private async waitForServerStartProbe(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      let timeout: NodeJS.Timeout;

      const onData = () => {
        clearTimeout(timeout);
        cleanup();
        resolve();
      };
      const onError = (e: Error) => {
        clearTimeout(timeout);
        cleanup();
        reject(e);
      };
      const onEnd = () => {
        clearTimeout(timeout);
        cleanup();
        reject(new Error("server exited prematurely"));
      };
      const cleanup = () => {
        this.serverStream.removeListener("data", onData);
        this.serverStream.removeListener("error", onError);
        this.serverStream.removeListener("end", onEnd);
      };

      timeout = setTimeout(
        () => {
          cleanup();
          resolve();
        },
        Math.min(500, this.options.deployTimeoutMs),
      );

      this.serverStream.on("data", onData);
      this.serverStream.once("error", onError);
      this.serverStream.once("end", onEnd);
    });
  }

  private async connectSockets(): Promise<void> {
    const order: SocketKind[] = [];
    if (this.options.video) order.push("video");
    if (this.options.audio) order.push("audio");
    if (this.options.control) order.push("control");

    const deadline = Date.now() + this.options.connectionTimeoutMs;
    for (const kind of order) {
      const socket = await this.connectSocket(kind, deadline);
      this.setSocket(kind, socket);
    }
  }

  private async connectSocket(
    kind: SocketKind,
    deadline: number,
  ): Promise<net.Socket> {
    let attempts = 0;
    while (Date.now() < deadline) {
      attempts++;
      try {
        return await this.adbClient.openLocal(
          this.device,
          this.options.socketName,
        );
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    throw new Error(
      `failed to connect scrcpy ${kind} socket after ${attempts} attempts`,
    );
  }

  private setSocket(kind: SocketKind, socket: net.Socket): void {
    if (kind === "video") this.videoSocket = socket;
    if (kind === "audio") this.audioSocket = socket;
    if (kind === "control") this.controlSocket = socket;
  }

  private async handshake(): Promise<StreamMeta> {
    const firstSocket =
      this.videoSocket || this.audioSocket || this.controlSocket;
    if (!firstSocket) throw new Error("no sockets connected");

    // scrcpy writes the dummy byte and device name to the first enabled socket,
    // then writes per-stream metadata on each stream socket in connection order.
    const sharedReader = new BufferedStreamReader(firstSocket);
    const dummy = await sharedReader.readExact(1);
    if (dummy[0] !== 0) throw new Error("invalid dummy byte");

    const deviceNameRaw = await sharedReader.readExact(64);
    const deviceName = zeroTerminatedUtf8Decode(deviceNameRaw);

    let videoCodec: VideoCodec | undefined;
    let audioCodec: AudioCodec | undefined;
    let width = 0;
    let height = 0;

    if (this.videoSocket) {
      this.videoReader =
        this.videoSocket === firstSocket
          ? sharedReader
          : new BufferedStreamReader(this.videoSocket);
      const codecIdBuf = await this.videoReader.readExact(4);
      videoCodec = VIDEO_CODEC_IDS[dataView(codecIdBuf).getUint32(0)];

      const sessionBuf = await this.videoReader.readExact(12);
      const session = this.options.protocol.parseFrameHeader(
        sessionBuf,
      ) as SessionPacket;
      width = session.width;
      height = session.height;
    }

    if (this.audioSocket) {
      this.audioReader =
        this.audioSocket === firstSocket
          ? sharedReader
          : new BufferedStreamReader(this.audioSocket);
      const codecIdBuf = await this.audioReader.readExact(4);
      const codecId = dataView(codecIdBuf).getUint32(0);
      if (codecId === 0) {
        this.audioSocket.destroy();
        this.audioSocket = null;
        this.audioReader = null;
      } else {
        audioCodec = AUDIO_CODEC_IDS[codecId];
      }
    }

    if (this.controlSocket) {
      this.controlReader =
        this.controlSocket === firstSocket
          ? sharedReader
          : new BufferedStreamReader(this.controlSocket);
    }

    return { deviceName, videoCodec, audioCodec, width, height };
  }

  private async socketReadExact(
    socket: net.Socket,
    n: number,
  ): Promise<Uint8Array> {
    if (this.videoSocket === socket && this.videoReader)
      return this.videoReader.readExact(n);
    if (this.audioSocket === socket && this.audioReader)
      return this.audioReader.readExact(n);
    if (this.controlSocket === socket && this.controlReader) {
      return this.controlReader.readExact(n);
    }
    throw new Error("no reader for socket");
  }

  private startWorkerLoops(): void {
    if (this.videoSocket) {
      this.workerLoop(
        "video",
        this.videoSocket,
        this.handleVideoHeader.bind(this),
      );
    }
    if (this.audioSocket) {
      this.workerLoop(
        "audio",
        this.audioSocket,
        this.handleAudioHeader.bind(this),
      );
    }
    if (this.controlSocket) this.controlWorkerLoop();
  }

  private async workerLoop(
    kind: "video" | "audio",
    socket: net.Socket,
    headerHandler: (header: Uint8Array) => Promise<void>,
  ): Promise<void> {
    try {
      while (this.running) {
        const header = await this.socketReadExact(socket, 12);
        await headerHandler(header);
      }
    } catch (e) {
      if (this.running) this.emit("error", e);
    }
  }

  private async handleVideoHeader(header: Uint8Array): Promise<void> {
    const parsed = this.options.protocol.parseFrameHeader(header);
    if (parsed.kind === "session") {
      // Session packets carry resolution changes and do not have a payload.
      this.emit("session", parsed);
      return;
    }
    const payload = await this.socketReadExact(this.videoSocket!, parsed.size);
    this.emit("video", parsed as FrameMeta, payload);
  }

  private async handleAudioHeader(header: Uint8Array): Promise<void> {
    const parsed = this.options.protocol.parseFrameHeader(header);
    if (parsed.kind === "session") return;
    const payload = await this.socketReadExact(this.audioSocket!, parsed.size);
    this.emit("audio", parsed as FrameMeta, payload);
  }

  private async controlWorkerLoop(): Promise<void> {
    const readExact = (n: number) =>
      this.socketReadExact(this.controlSocket!, n);
    try {
      while (this.running) {
        const msg: DeviceMessage =
          await this.options.protocol.parseDeviceMessage(readExact);
        this.emit("deviceMessage", msg);
      }
    } catch (e) {
      if (this.running) this.emit("error", e);
    }
  }

  private cleanup(): void {
    [this.videoSocket, this.audioSocket, this.controlSocket].forEach((socket) =>
      socket?.destroy(),
    );
    this.videoSocket = null;
    this.audioSocket = null;
    this.controlSocket = null;
    this.videoReader = null;
    this.audioReader = null;
    this.controlReader = null;
    this.serverStream?.destroy();
    this.serverStream = null;
  }
}
