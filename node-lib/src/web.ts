import { WebSocket, WebSocketServer, RawData } from "ws";
import { MediaPacket, MediaKind, ScrcpyV4Service } from "./service.js";
import {
  ControlMessage,
  ControlMessageType,
  ACTION_DOWN,
  ACTION_MOVE,
  ACTION_UP,
  BUTTON_PRIMARY,
  KEY_ACTION_DOWN,
  KEY_ACTION_UP,
  POINTER_ID_GENERIC_FINGER,
  POINTER_ID_MOUSE,
} from "./protocol.js";

export interface WebOptions {
  port?: number;
  server?: any; // http.Server
  path?: string;
}

const HEADER_SIZE = 16;
const KIND_VIDEO = 1;
const KIND_AUDIO = 2;
const KIND_SESSION = 3;

export class ScrcpyV4WebBridge {
  private wss: WebSocketServer;

  constructor(private service: ScrcpyV4Service, options: WebOptions = {}) {
    this.wss = new WebSocketServer({
      port: options.port,
      server: options.server,
      path: options.path || "/ws/scrcpy",
    });

    this.wss.on("connection", (ws: WebSocket) => this.handleConnection(ws));
  }

  private async handleConnection(ws: WebSocket): Promise<void> {
    // Send init message
    const meta = this.service.currentMeta;
    const initMsg = {
      type: "init",
      state: this.service.currentState,
      device: {
        name: meta?.deviceName || null,
        width: meta?.width || null,
        height: meta?.height || null,
        video_codec: meta?.videoCodec || null,
        audio_codec: meta?.audioCodec || null,
      },
      binary_header: {
        size: HEADER_SIZE,
        kinds: { video: KIND_VIDEO, audio: KIND_AUDIO, session: KIND_SESSION },
        flags: {
          config: 0x01,
          key_frame: 0x02,
          client_resized: 0x04,
        },
      },
    };
    ws.send(JSON.stringify(initMsg));

    const subscription = this.service.subscribe();
    
    ws.on("message", (data: RawData) => {
      try {
        const payload = JSON.parse(data.toString());
        const msg = this.parseControlJson(payload);
        if (msg) {
          this.service.sendControlMessage(msg);
        }
      } catch (e: any) {
        console.error("failed to handle control message", e);
        ws.send(JSON.stringify({ type: "error", message: e.message }));
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

  private parseControlJson(payload: any): ControlMessage | null {
    const type = (payload.type || "").toLowerCase();

    switch (type) {
      case "touch": {
        const action = this.parseTouchAction(payload.action);
        const pointerId = this.parsePointerId(payload.pointerId);
        return {
          type: ControlMessageType.INJECT_TOUCH_EVENT,
          action,
          pointerId,
          x: payload.x,
          y: payload.y,
          screenWidth: payload.screenWidth,
          screenHeight: payload.screenHeight,
          pressure: payload.pressure ?? (action !== ACTION_UP ? 1.0 : 0.0),
          actionButton: payload.actionButton ?? (action === ACTION_DOWN || action === ACTION_UP ? BUTTON_PRIMARY : 0),
          buttons: payload.buttons ?? (action !== ACTION_UP ? BUTTON_PRIMARY : 0),
        };
      }
      case "scroll":
        return {
          type: ControlMessageType.INJECT_SCROLL_EVENT,
          x: payload.x,
          y: payload.y,
          screenWidth: payload.screenWidth,
          screenHeight: payload.screenHeight,
          hScroll: payload.hScroll || 0.0,
          vScroll: payload.vScroll || 0.0,
          buttons: payload.buttons || 0,
        };
      case "key":
        return {
          type: ControlMessageType.INJECT_KEYCODE,
          action: this.parseKeyAction(payload.action),
          keycode: payload.keycode,
          repeat: payload.repeat || 0,
          metaState: payload.metaState || 0,
        };
      case "text":
        return { type: ControlMessageType.INJECT_TEXT, text: payload.text || "" };
      case "back":
        return { type: ControlMessageType.BACK_OR_SCREEN_ON, action: this.parseKeyAction(payload.action) };
      case "home":
        return { type: ControlMessageType.INJECT_KEYCODE, action: this.parseKeyAction(payload.action), keycode: 3 };
      case "app_switch":
        return { type: ControlMessageType.INJECT_KEYCODE, action: this.parseKeyAction(payload.action), keycode: 187 };
      case "power":
        return { type: ControlMessageType.INJECT_KEYCODE, action: this.parseKeyAction(payload.action), keycode: 26 };
      case "expand_notifications":
        return { type: ControlMessageType.EXPAND_NOTIFICATION_PANEL };
      case "expand_settings":
        return { type: ControlMessageType.EXPAND_SETTINGS_PANEL };
      case "collapse_panels":
        return { type: ControlMessageType.COLLAPSE_PANELS };
      case "rotate":
        return { type: ControlMessageType.ROTATE_DEVICE };
      case "reset_video":
        return { type: ControlMessageType.RESET_VIDEO };
      case "set_clipboard":
        return {
          type: ControlMessageType.SET_CLIPBOARD,
          sequence: BigInt(payload.sequence || 0),
          text: payload.text || "",
          paste: !!payload.paste,
        };
      case "set_display_power":
        return { type: ControlMessageType.SET_DISPLAY_POWER, on: !!payload.on };
      default:
        console.warn(`unsupported control message type: ${type}`);
        return null;
    }
  }

  private parseTouchAction(value: any): number {
    if (typeof value === "number") return value;
    const text = String(value).toLowerCase();
    if (text === "down" || text === "pointer_down") return ACTION_DOWN;
    if (text === "up" || text === "pointer_up") return ACTION_UP;
    if (text === "move") return ACTION_MOVE;
    throw new Error(`unknown touch action: ${value}`);
  }

  private parseKeyAction(value: any): number {
    if (typeof value === "number") return value;
    const text = String(value).toLowerCase();
    if (text === "down") return KEY_ACTION_DOWN;
    if (text === "up") return KEY_ACTION_UP;
    throw new Error(`unknown key action: ${value}`);
  }

  private parsePointerId(value: any): bigint {
    if (value === null || value === undefined) return POINTER_ID_GENERIC_FINGER;
    if (typeof value === "string") {
      const text = value.toLowerCase();
      if (text === "mouse") return POINTER_ID_MOUSE;
      if (text === "finger") return POINTER_ID_GENERIC_FINGER;
      return BigInt(text);
    }
    return BigInt(value);
  }

  private serializePacket(packet: MediaPacket): Buffer {
    // Binary frame layout for server -> client media (matching Python):
    //     byte  0      kind       1 = video, 2 = audio, 3 = video session
    //     byte  1      flags      bit 0 = config packet (codec extradata)
    //                             bit 1 = key frame
    //                             bit 2 = client_resized (session only)
    //     bytes 2-3    reserved   currently 0
    //     bytes 4-7    payload size (uint32 BE)
    //     bytes 8-15   pts in microseconds (uint64 BE)
    //                  - for SESSION frames, the upper 32 bits encode width and
    //                    the lower 32 bits encode height.
    //     bytes 16+    payload

    let kind = KIND_VIDEO;
    let flags = 0;
    let pts = packet.ptsUs;
    let size = packet.payload.length;
    let payload = packet.payload;

    if (packet.config) flags |= 0x01;
    if (packet.keyFrame) flags |= 0x02;

    if (packet.kind === MediaKind.AUDIO) {
      kind = KIND_AUDIO;
    } else if (packet.kind === MediaKind.SESSION) {
      kind = KIND_SESSION;
      size = 0;
      payload = Buffer.alloc(0);
      // Upper 32 bits width, lower 32 bits height
      pts = (BigInt(packet.width || 0) << 32n) | BigInt(packet.height || 0);
    }

    const buf = Buffer.alloc(HEADER_SIZE + payload.length);
    buf.writeUInt8(kind, 0);
    buf.writeUInt8(flags, 1);
    buf.writeUInt16BE(0, 2); // reserved
    buf.writeUInt32BE(size, 4);
    buf.writeBigUInt64BE(pts, 8);
    payload.copy(buf, HEADER_SIZE);

    return buf;
  }

  close(): void {
    this.wss.close();
  }
}
