import { EventEmitter } from "node:events";
import { ScrcpyBackend, StreamMeta } from "../backend/scrcpy-backend.js";
import {
  ControlMessage,
  DeviceMessage,
  FrameMeta,
  SessionPacket,
} from "../protocol/index.js";
import { PacketQueueSubscriber } from "./packet-queue-subscriber.js";
import {
  MediaKind,
  MediaPacket,
  ScrcpyStreamServiceOptions,
  StreamState,
} from "./types.js";

const EMPTY_PAYLOAD = new Uint8Array(0);

export class ScrcpyStreamService extends EventEmitter {
  private backend: ScrcpyBackend;
  private state = StreamState.STOPPED;
  private meta: StreamMeta | null = null;
  private queueMaxPackets: number;

  private videoConfig: Uint8Array | null = null;
  private audioConfig: Uint8Array | null = null;
  private latestKeyFrame: MediaPacket | null = null;
  private latestSession: SessionPacket | null = null;

  private subscribers: Set<PacketQueueSubscriber> = new Set();

  constructor(options: ScrcpyStreamServiceOptions = {}) {
    super();
    this.queueMaxPackets = options.queueMaxPackets ?? 240;
    this.backend = new ScrcpyBackend(options);
    this.setupBackendListeners();
  }

  async start(): Promise<StreamMeta> {
    if (
      this.state === StreamState.RUNNING ||
      this.state === StreamState.STARTING
    ) {
      return this.meta!;
    }
    this.setState(StreamState.STARTING);
    try {
      this.meta = await this.backend.start();
      this.setState(StreamState.RUNNING);
      return this.meta;
    } catch (e) {
      this.setState(StreamState.ERROR);
      throw e;
    }
  }

  stop(): void {
    if (this.state === StreamState.STOPPED) return;
    this.setState(StreamState.STOPPING);
    this.backend.stop();
    this.videoConfig = null;
    this.audioConfig = null;
    this.latestKeyFrame = null;
    this.latestSession = null;
    this.subscribers.forEach((subscriber) => subscriber.close());
    this.subscribers.clear();
    this.setState(StreamState.STOPPED);
  }

  sendControlMessage(msg: ControlMessage): void {
    this.backend.sendControlMessage(msg);
  }

  subscribe(): AsyncIterableIterator<MediaPacket> {
    const subscriber = new PacketQueueSubscriber(this.queueMaxPackets);
    this.subscribers.add(subscriber);
    this.pushSnapshot(subscriber);

    const iterator = subscriber[Symbol.asyncIterator]();
    return {
      next: () => iterator.next(),
      return: async (value?: any) => {
        this.subscribers.delete(subscriber);
        subscriber.close();
        if (iterator.return) return iterator.return(value);
        return { done: true, value };
      },
      throw: async (e?: any) => {
        this.subscribers.delete(subscriber);
        subscriber.close();
        if (iterator.throw) return iterator.throw(e);
        throw e;
      },
      [Symbol.asyncIterator]() {
        return this;
      },
    };
  }

  get currentState(): StreamState {
    return this.state;
  }

  get currentMeta(): StreamMeta | null {
    return this.meta;
  }

  private setupBackendListeners(): void {
    this.backend.on("video", (meta: FrameMeta, payload: Uint8Array) => {
      const packet: MediaPacket = {
        kind: MediaKind.VIDEO,
        ptsUs: meta.ptsUs,
        config: meta.config,
        keyFrame: meta.keyFrame,
        payload,
      };
      if (meta.config) this.videoConfig = payload;
      if (meta.keyFrame) this.latestKeyFrame = packet;
      this.broadcast(packet);
    });

    this.backend.on("audio", (meta: FrameMeta, payload: Uint8Array) => {
      const packet: MediaPacket = {
        kind: MediaKind.AUDIO,
        ptsUs: meta.ptsUs,
        config: meta.config,
        keyFrame: false,
        payload,
      };
      if (meta.config) this.audioConfig = payload;
      this.broadcast(packet);
    });

    this.backend.on("session", (session: SessionPacket) => {
      this.latestSession = session;
      this.broadcast({
        kind: MediaKind.SESSION,
        ptsUs: 0n,
        config: false,
        keyFrame: false,
        payload: EMPTY_PAYLOAD,
        width: session.width,
        height: session.height,
      });
    });

    this.backend.on("deviceMessage", (msg: DeviceMessage) => {
      this.emit("deviceMessage", msg);
    });

    this.backend.on("error", (e: Error) => {
      this.setState(StreamState.ERROR);
      this.emit("error", e);
      this.stop();
    });
  }

  private pushSnapshot(subscriber: PacketQueueSubscriber): void {
    if (this.latestSession) {
      subscriber.push({
        kind: MediaKind.SESSION,
        ptsUs: 0n,
        config: false,
        keyFrame: false,
        payload: EMPTY_PAYLOAD,
        width: this.latestSession.width,
        height: this.latestSession.height,
      });
    }
    if (this.videoConfig) {
      subscriber.push({
        kind: MediaKind.VIDEO,
        ptsUs: 0n,
        config: true,
        keyFrame: false,
        payload: this.videoConfig,
      });
    }
    if (this.latestKeyFrame) subscriber.push(this.latestKeyFrame);
    if (this.audioConfig) {
      subscriber.push({
        kind: MediaKind.AUDIO,
        ptsUs: 0n,
        config: true,
        keyFrame: false,
        payload: this.audioConfig,
      });
    }
  }

  private broadcast(packet: MediaPacket): void {
    for (const subscriber of this.subscribers) {
      subscriber.push(packet);
    }
  }

  private setState(state: StreamState): void {
    if (this.state === state) return;
    this.state = state;
    this.emit("state", state);
  }
}
