import { ControlMessage } from "../v4_0/control-message-types.js";
import { DeviceMessage } from "../v4_0/device-message.js";
import { StreamPacketMeta } from "../v4_0/frame-header.js";
import { ScrcpyServerOptionInput } from "../v4_0/server-options.js";

export type ScrcpyProtocolVersion = "4.0";

export interface ScrcpyProtocol {
  version: ScrcpyProtocolVersion;
  serverVersion: string;
  serverJarAssetName: string;
  serializeControlMessage(message: ControlMessage): Uint8Array;
  parseFrameHeader(header: Uint8Array): StreamPacketMeta;
  parseDeviceMessage(
    readExact: (n: number) => Promise<Uint8Array>,
  ): Promise<DeviceMessage>;
  buildServerOptions(input: ScrcpyServerOptionInput): string[];
  getSocketName(scid?: number): string;
}
