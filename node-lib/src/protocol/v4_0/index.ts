import { serializeControlMessage } from "./control-message.js";
import { parseDeviceMessage } from "./device-message.js";
import { parseFrameHeader } from "./frame-header.js";
import {
  buildServerOptions,
  getSocketName,
  SCRCPY_4_0_SERVER_JAR,
  SCRCPY_4_0_SERVER_VERSION,
} from "./server-options.js";
import { ScrcpyProtocol } from "../core/types.js";

export * from "./codecs.js";
export * from "./control-message.js";
export * from "./control-message-types.js";
export * from "./device-message.js";
export * from "./frame-header.js";
export * from "./server-options.js";
export * from "./string-payload.js";

export const SCRCPY_4_0_PROTOCOL: ScrcpyProtocol = {
  version: "4.0",
  serverVersion: SCRCPY_4_0_SERVER_VERSION,
  serverJarAssetName: SCRCPY_4_0_SERVER_JAR,
  serializeControlMessage,
  parseFrameHeader,
  parseDeviceMessage,
  buildServerOptions,
  getSocketName,
};
