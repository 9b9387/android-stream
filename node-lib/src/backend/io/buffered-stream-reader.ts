import * as net from "node:net";
import { concatBytes } from "../../protocol/core/binary.js";

interface ReadWaiter {
  n: number;
  resolve: (bytes: Uint8Array) => void;
  reject: (err: Error) => void;
}

export class BufferedStreamReader {
  private buffer: Uint8Array<ArrayBufferLike> = new Uint8Array(0);
  private waiters: ReadWaiter[] = [];
  private ended = false;

  constructor(stream: net.Socket) {
    stream.on("data", (chunk: Uint8Array) => {
      this.buffer = concatBytes([this.buffer, chunk as Uint8Array]);
      this.checkWaiters();
    });
    stream.on("error", (err) => {
      this.rejectAll(err);
    });
    stream.on("end", () => {
      this.ended = true;
      this.rejectAll(new Error("stream ended"));
    });
  }

  readExact(n: number): Promise<Uint8Array> {
    if (this.ended) return Promise.reject(new Error("stream already ended"));
    return new Promise((resolve, reject) => {
      this.waiters.push({ n, resolve, reject });
      this.checkWaiters();
    });
  }

  private rejectAll(err: Error): void {
    this.waiters.forEach((waiter) => waiter.reject(err));
    this.waiters = [];
  }

  private checkWaiters(): void {
    while (
      this.waiters.length > 0 &&
      this.buffer.byteLength >= this.waiters[0].n
    ) {
      const waiter = this.waiters.shift()!;
      const chunk = this.buffer.subarray(0, waiter.n);
      this.buffer = this.buffer.subarray(waiter.n);
      waiter.resolve(chunk);
    }
  }
}
