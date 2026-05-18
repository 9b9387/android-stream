import { dataView, utf8Decode } from "../core/binary.js";

export enum DeviceMessageType {
  CLIPBOARD = 0,
  ACK_CLIPBOARD = 1,
  UHID_OUTPUT = 2,
}

export type DeviceMessage =
  | {
      type: DeviceMessageType.CLIPBOARD;
      text: string;
    }
  | {
      type: DeviceMessageType.ACK_CLIPBOARD;
      sequence: bigint;
    }
  | {
      type: DeviceMessageType.UHID_OUTPUT;
      uhidId: number;
      data: Uint8Array;
    };

export async function parseDeviceMessage(
  readExact: (n: number) => Promise<Uint8Array>,
): Promise<DeviceMessage> {
  const head = await readExact(1);
  const type = head[0] as DeviceMessageType;

  switch (type) {
    case DeviceMessageType.CLIPBOARD: {
      const lenBuf = await readExact(4);
      const length = dataView(lenBuf).getUint32(0);
      const textBuf = await readExact(length);
      return { type, text: utf8Decode(textBuf) };
    }
    case DeviceMessageType.ACK_CLIPBOARD: {
      const seqBuf = await readExact(8);
      return { type, sequence: dataView(seqBuf).getBigUint64(0) };
    }
    case DeviceMessageType.UHID_OUTPUT: {
      const metaBuf = await readExact(4);
      const meta = dataView(metaBuf);
      const uhidId = meta.getUint16(0);
      const length = meta.getUint16(2);
      const data = await readExact(length);
      return { type, uhidId, data };
    }
    default:
      throw new Error(`unknown device message type: ${type}`);
  }
}
