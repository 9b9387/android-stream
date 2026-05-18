import { MediaKind, MediaPacket } from "./types.js";

export class PacketQueueSubscriber implements AsyncIterable<MediaPacket> {
  private queue: MediaPacket[] = [];
  private resolveNext: ((value: IteratorResult<MediaPacket>) => void) | null =
    null;
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
      // Preserve config/session packets because late subscribers need them to
      // initialize decoders before receiving regular media frames.
      const index = this.queue.findIndex(
        (item) => !item.config && item.kind !== MediaKind.SESSION,
      );
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

      const result = await new Promise<IteratorResult<MediaPacket>>(
        (resolve) => {
          this.resolveNext = resolve;
        },
      );
      if (result.done) return;
      yield result.value;
    }
  }
}
