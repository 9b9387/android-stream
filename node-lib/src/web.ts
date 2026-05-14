import { WebSocket, WebSocketServer, RawData } from "ws";
import { MediaPacket, ScrcpyV4Service } from "./service.js";
import { ControlMessage } from "./protocol.js";

export interface WebOptions {
  port?: number;
  server?: any; // http.Server
  path?: string;
}

export class ScrcpyV4WebBridge {
  private wss: WebSocketServer;

  constructor(private service: ScrcpyV4Service, options: WebOptions = {}) {
    this.wss = new WebSocketServer({
      port: options.port,
      server: options.server,
      path: options.path || "/scrcpy",
    });

    this.wss.on("connection", (ws: WebSocket) => this.handleConnection(ws));
  }

  private async handleConnection(ws: WebSocket): Promise<void> {
    const subscription = this.service.subscribe();
    
    ws.on("message", (data: RawData) => {
      try {
        const msg = JSON.parse(data.toString()) as ControlMessage;
        this.service.sendControlMessage(msg);
      } catch (e) {
        console.error("failed to handle control message", e);
      }
    });

    ws.on("close", () => {
      subscription.return?.();
    });

    try {
      for await (const packet of subscription) {
        if (ws.readyState !== WebSocket.OPEN) break;
        
        const buffer = this.serializePacket(packet);
        ws.send(buffer);
      }
    } catch (e) {
      console.error("subscription loop error", e);
      ws.close();
    }
  }

  private serializePacket(packet: MediaPacket): Buffer {
    // Binary framing:
    // [0] kind (0: video, 1: audio, 2: session)
    // [1-8] pts (bigint)
    // [9] flags (bit 0: config, bit 1: keyFrame)
    // [10-13] width (if session)
    // [14-17] height (if session)
    // [10...] payload
    
    let offset = 10;
    if (packet.kind === "session") offset = 18;

    const buf = Buffer.alloc(offset + packet.payload.length);
    let kind = 0;
    if (packet.kind === "audio") kind = 1;
    if (packet.kind === "session") kind = 2;

    buf.writeUInt8(kind, 0);
    buf.writeBigUInt64BE(packet.ptsUs, 1);
    
    let flags = 0;
    if (packet.config) flags |= 0x01;
    if (packet.keyFrame) flags |= 0x02;
    buf.writeUInt8(flags, 9);

    if (packet.kind === "session") {
      buf.writeUInt32BE(packet.width || 0, 10);
      buf.writeUInt32BE(packet.height || 0, 14);
    }

    packet.payload.copy(buf, offset);
    return buf;
  }

  close(): void {
    this.wss.close();
  }
}
