import { EventEmitter } from "node:events";
import * as net from "node:net";
import adbkit from "@devicefarmer/adbkit";

const Adb = (adbkit as any).default || adbkit;

export interface SelectedAdbDevice {
  serial: string;
  device: any;
}

export class ScrcpyAdbClient {
  private adb = Adb.createClient();

  async selectDevice(deviceSerial?: string): Promise<SelectedAdbDevice> {
    const devices = await this.adb.listDevices();
    if (devices.length === 0) throw new Error("no ADB device found");

    const serial = deviceSerial || devices[0].id;
    return {
      serial,
      device: this.adb.getDevice(serial),
    };
  }

  async pushServerJar(
    device: any,
    localPath: string,
    remotePath: string,
    timeoutMs: number,
  ): Promise<void> {
    const transfer = await device.push(localPath, remotePath);
    await this.waitForTransfer(transfer, timeoutMs);
  }

  async startServer(device: any, command: string): Promise<any> {
    return device.shell(command);
  }

  async openLocal(device: any, socketName: string): Promise<net.Socket> {
    return device.openLocal(`localabstract:${socketName}`);
  }

  private async waitForTransfer(
    transfer: EventEmitter,
    timeoutMs: number,
  ): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        cleanup();
        reject(new Error(`server push timeout after ${timeoutMs}ms`));
      }, timeoutMs);

      const onEnd = () => {
        clearTimeout(timeout);
        cleanup();
        resolve();
      };
      const onError = (e: Error) => {
        clearTimeout(timeout);
        cleanup();
        reject(e);
      };
      const cleanup = () => {
        transfer.removeListener("end", onEnd);
        transfer.removeListener("error", onError);
      };

      transfer.once("end", onEnd);
      transfer.once("error", onError);
    });
  }
}
