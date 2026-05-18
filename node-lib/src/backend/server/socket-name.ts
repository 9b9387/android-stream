import { getScrcpyProtocol } from "../../protocol/registry.js";
import { ScrcpyProtocolVersion } from "../../protocol/core/types.js";

export function getScrcpySocketName(
  scid?: number,
  protocolVersion: ScrcpyProtocolVersion = "4.0",
): string {
  return getScrcpyProtocol(protocolVersion).getSocketName(scid);
}
