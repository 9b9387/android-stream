export interface WebSocketBridgeOptions {
  port?: number;
  server?: any;
  path?: string;
}

export const WEBSOCKET_HEADER_SIZE = 16;
export const WEBSOCKET_KIND_VIDEO = 1;
export const WEBSOCKET_KIND_AUDIO = 2;
export const WEBSOCKET_KIND_SESSION = 3;
