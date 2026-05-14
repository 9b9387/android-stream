import { EventEmitter } from "node:events";
import {
  BackendOptions,
  ScrcpyV4Backend,
  StreamMeta,
} from "./backend.js";
import {
  ControlMessage,
  DeviceMessage,
  FrameMeta,
  SessionPacket,
} from "./protocol.js";

export enum StreamState {
  STOPPED = "stopped",
  STARTING = "starting",
  RUNNING = "running",
  STOPPING = "stopping",
  ERROR = "error",
}

export enum MediaKind {
  VIDEO = "video",
  AUDIO = "audio",
  SESSION = "session",
}

export interface MediaPacket {
  kind: MediaKind;
  ptsUs: bigint;
  config: boolean;
  keyFrame: boolean;
  payload: Buffer;
  width?: number;
  height?: number;
}

export interface ServiceOptions extends BackendOptions {
  queueMaxPackets?: number;
}

export class ScrcpyV4Service extends EventEmitter {
  private backend: ScrcpyV4Backend;
  private state = StreamState.STOPPED;
  private meta: StreamMeta | null = null;
  private options: Required<ServiceOptions>;

  private videoConfig: Buffer | null = null;
  private audioConfig: Buffer | null = null;
  private latestKeyFrame: MediaPacket | null = null;
  private latestSession: SessionPacket | null = null;

  private subscribers: Set<MediaSubscriber> = new Set();

  constructor(options: ServiceOptions = {}) {
    super();
    this.options = {
      queueMaxPackets: options.queueMaxPackets || 240,
      ...options,
    } as Required<ServiceOptions>;

    this.backend = new ScrcpyV4Backend(this.options);
    this.setupBackendListeners();
  }

  private setupBackendListeners(): void {
    this.backend.on("video", (meta: FrameMeta, payload: Buffer) => {
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

    this.backend.on("audio", (meta: FrameMeta, payload: Buffer) => {
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
      const packet: MediaPacket = {
        kind: MediaKind.SESSION,
        ptsUs: 0n,
        config: false,
        keyFrame: false,
        payload: Buffer.alloc(0),
        width: session.width,
        height: session.height,
      };
      this.broadcast(packet);
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

  async start(): Promise<StreamMeta> {
    if (this.state === StreamState.RUNNING || this.state === StreamState.STARTING) {
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
    this.subscribers.forEach(sub => sub.close());
    this.subscribers.clear();
    this.setState(StreamState.STOPPED);
  }

  sendControlMessage(msg: ControlMessage): void {
    this.backend.sendControlMessage(msg);
  }

  subscribe(): AsyncIterableIterator<MediaPacket> {
    const subscriber = new MediaSubscriber(this.options.queueMaxPackets);
    this.subscribers.add(subscriber);

    // Send initial snapshot
    if (this.latestSession) {
      subscriber.push({
        kind: MediaKind.SESSION,
        ptsUs: 0n,
        config: false,
        keyFrame: false,
        payload: Buffer.alloc(0),
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
    if (this.latestKeyFrame) {
      subscriber.push(this.latestKeyFrame);
    }
    if (this.audioConfig) {
      subscriber.push({
        kind: MediaKind.AUDIO,
        ptsUs: 0n,
        config: true,
        keyFrame: false,
        payload: this.audioConfig,
      });
    }

    const iterator = subscriber[Symbol.asyncIterator]();
    
    return {
      next: () => iterator.next(),
      return: async (value?: any) => {
        this.subscribers.delete(subscriber);
        subscriber.close();
        if (iterator.return) return await iterator.return(value);
        return { done: true, value };
      },
      throw: async (e?: any) => {
        this.subscribers.delete(subscriber);
        subscriber.close();
        if (iterator.throw) return await iterator.throw(e);
        throw e;
      },
      [Symbol.asyncIterator]() {
        return this;
      },
    };
  }

  private broadcast(packet: MediaPacket): void {
    for (const sub of this.subscribers) {
      sub.push(packet);
    }
  }

  private setState(state: StreamState): void {
    if (this.state === state) return;
    this.state = state;
    this.emit("state", state);
  }

  get currentState() { return this.state; }
  get currentMeta() { return this.meta; }
}

class MediaSubscriber implements AsyncIterable<MediaPacket> {
  private queue: MediaPacket[] = [];
  private resolveNext: ((value: IteratorResult<MediaPacket>) => void) | null = null;
  private closed = false;

  constructor(private maxQueue: number) {}

  push(packet: MediaPacket): void {
    if (this.closed) return;

    if (this.resolveNext) {
      const resolve = this.resolveNext;
      this.resolveNext = null;
      resolve({ done: false, value: packet });
      return;
    }

    if (this.queue.length >= this.maxQueue) {
      // Drop oldest non-config, non-session packet
      const index = this.queue.findIndex(p => !p.config && p.kind !== MediaKind.SESSION);
      if (index !== -1) {
        this.queue.splice(index, 1);
      } else {
        this.queue.shift();
      }
    }
    this.queue.push(packet);
  }

  close(): void {
    this.closed = true;
    if (this.resolveNext) {
      const resolve = this.resolveNext;
      this.resolveNext = null;
      resolve({ done: true, value: undefined });
    }
  }

  async *[Symbol.asyncIterator](): AsyncIterator<MediaPacket> {
    while (true) {
      if (this.queue.length > 0) {
        yield this.queue.shift()!;
        continue;
      }
      if (this.closed) return;
      
      const result = await new Promise<IteratorResult<MediaPacket>>((resolve) => {
        this.resolveNext = resolve;
      });
      if (result.done) return;
      yield result.value;
    }
  }
}
