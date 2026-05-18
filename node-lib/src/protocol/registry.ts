import { ScrcpyProtocol, ScrcpyProtocolVersion } from "./core/types.js";
import { SCRCPY_4_0_PROTOCOL } from "./v4_0/index.js";

const PROTOCOLS: Record<ScrcpyProtocolVersion, ScrcpyProtocol> = {
  "4.0": SCRCPY_4_0_PROTOCOL,
};

export function getScrcpyProtocol(
  version: ScrcpyProtocolVersion = "4.0",
): ScrcpyProtocol {
  // Keep version selection centralized so future protocol versions can be
  // registered without changing backend or service orchestration code.
  const protocol = PROTOCOLS[version];
  if (!protocol) {
    throw new Error(`unsupported scrcpy protocol version: ${version}`);
  }
  return protocol;
}

export function listScrcpyProtocolVersions(): ScrcpyProtocolVersion[] {
  return Object.keys(PROTOCOLS) as ScrcpyProtocolVersion[];
}
