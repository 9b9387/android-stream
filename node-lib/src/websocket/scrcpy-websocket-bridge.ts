import { RawData, WebSocket, WebSocketServer } from "ws";
import { ScrcpyStreamService } from "../service/scrcpy-stream-service.js";
import { serializeMediaPacket } from "./binary-packet.js";
import { parseControlJson } from "./control-json.js";
import {
  WEBSOCKET_HEADER_SIZE,
  WEBSOCKET_KIND_AUDIO,
  WEBSOCKET_KIND_SESSION,
  WEBSOCKET_KIND_VIDEO,
  WebSocketBridgeOptions,
} from "./types.js";

export class ScrcpyWebSocketBridge {
  private wss: WebSocketServer;

  constructor(
    private service: ScrcpyStreamService,
    options: WebSocketBridgeOptions = {},
  ) {
    this.wss = new WebSocketServer({
      port: options.port,
      server: options.server,
      path: options.path || "/ws/scrcpy",
    });

    this.wss.on("connection", (ws: WebSocket) => this.handleConnection(ws));
  }

  close(): void {
    this.wss.close();
  }

  private async handleConnection(ws: WebSocket): Promise<void> {
    ws.send(JSON.stringify(this.createInitMessage()));
    const subscription = this.service.subscribe();

    ws.on("message", (data: RawData) => {
      try {
        const payload = JSON.parse(data.toString());
        const msg = parseControlJson(payload);
        if (msg) this.service.sendControlMessage(msg);
      } catch (e: any) {
        ws.send(JSON.stringify({ type: "error", message: e.message }));
      }
    });

    ws.on("close", () => {
      subscription.return?.();
    });

    try {
      for await (const packet of subscription) {
        if (ws.readyState !== WebSocket.OPEN) break;
        ws.send(serializeMediaPacket(packet));
      }
    } catch {
      ws.close();
    }
  }

  private createInitMessage() {
    const meta = this.service.currentMeta;
    return {
      type: "init",
      state: this.service.currentState,
      device: {
        name: meta?.deviceName ?? null,
        width: meta?.width ?? null,
        height: meta?.height ?? null,
        video_codec: meta?.videoCodec ?? null,
        audio_codec: meta?.audioCodec ?? null,
      },
      binary_header: {
        size: WEBSOCKET_HEADER_SIZE,
        kinds: {
          video: WEBSOCKET_KIND_VIDEO,
          audio: WEBSOCKET_KIND_AUDIO,
          session: WEBSOCKET_KIND_SESSION,
        },
        flags: {
          config: 0x01,
          key_frame: 0x02,
          client_resized: 0x04,
        },
      },
    };
  }
}
