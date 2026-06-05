export interface WebSocketBridgeOptions {
  port?: number;
  server?: any;
  path?: string;
  /**
   * Drop media frames for a connection whose outbound buffer exceeds this many
   * bytes. Protects the process from unbounded memory growth when a client is
   * slower than the stream. 0 disables. Defaults to 8MiB.
   */
  maxBufferedBytes?: number;
}

export const WEBSOCKET_HEADER_SIZE = 16;
export const WEBSOCKET_KIND_VIDEO = 1;
export const WEBSOCKET_KIND_AUDIO = 2;
export const WEBSOCKET_KIND_SESSION = 3;
