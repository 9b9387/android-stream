export enum ControlMessageType {
  INJECT_KEYCODE = 0,
  INJECT_TEXT = 1,
  INJECT_TOUCH_EVENT = 2,
  INJECT_SCROLL_EVENT = 3,
  BACK_OR_SCREEN_ON = 4,
  EXPAND_NOTIFICATION_PANEL = 5,
  EXPAND_SETTINGS_PANEL = 6,
  COLLAPSE_PANELS = 7,
  GET_CLIPBOARD = 8,
  SET_CLIPBOARD = 9,
  SET_DISPLAY_POWER = 10,
  ROTATE_DEVICE = 11,
  UHID_CREATE = 12,
  UHID_INPUT = 13,
  UHID_DESTROY = 14,
  OPEN_HARD_KEYBOARD_SETTINGS = 15,
  START_APP = 16,
  RESET_VIDEO = 17,
  CAMERA_SET_TORCH = 18,
  CAMERA_ZOOM_IN = 19,
  CAMERA_ZOOM_OUT = 20,
  RESIZE_DISPLAY = 21,
}

export enum CopyKey {
  NONE = 0,
  COPY = 1,
  CUT = 2,
}

export const ACTION_DOWN = 0;
export const ACTION_UP = 1;
export const ACTION_MOVE = 2;

export const KEY_ACTION_DOWN = 0;
export const KEY_ACTION_UP = 1;

export const BUTTON_PRIMARY = 1 << 0;
export const BUTTON_SECONDARY = 1 << 1;
export const BUTTON_TERTIARY = 1 << 2;

export const POINTER_ID_MOUSE = -1n;
export const POINTER_ID_GENERIC_FINGER = -2n;
export const POINTER_ID_VIRTUAL_FINGER = -3n;

export const INJECT_TEXT_MAX_LENGTH = 300;
export const CONTROL_MESSAGE_MAX_SIZE = 1 << 18;
export const SET_CLIPBOARD_TEXT_MAX_LENGTH = CONTROL_MESSAGE_MAX_SIZE - 14;
export const UHID_NAME_MAX_LENGTH = 127;
export const START_APP_NAME_MAX_LENGTH = 255;

export type ControlMessage =
  | {
      type: ControlMessageType.INJECT_KEYCODE;
      action: number;
      keycode: number;
      repeat?: number;
      metaState?: number;
    }
  | {
      type: ControlMessageType.INJECT_TEXT;
      text: string;
    }
  | {
      type: ControlMessageType.INJECT_TOUCH_EVENT;
      action: number;
      pointerId: bigint;
      x: number;
      y: number;
      screenWidth: number;
      screenHeight: number;
      pressure?: number;
      actionButton?: number;
      buttons?: number;
    }
  | {
      type: ControlMessageType.INJECT_SCROLL_EVENT;
      x: number;
      y: number;
      screenWidth: number;
      screenHeight: number;
      hScroll?: number;
      vScroll?: number;
      buttons?: number;
    }
  | {
      type: ControlMessageType.BACK_OR_SCREEN_ON;
      action: number;
    }
  | {
      type: ControlMessageType.GET_CLIPBOARD;
      copyKey: CopyKey | number;
    }
  | {
      type: ControlMessageType.SET_CLIPBOARD;
      sequence: bigint;
      text: string;
      paste: boolean;
    }
  | {
      type: ControlMessageType.SET_DISPLAY_POWER;
      on: boolean;
    }
  | {
      type: ControlMessageType.UHID_CREATE;
      id: number;
      vendorId: number;
      productId: number;
      name: string;
      reportDescriptor: Uint8Array;
    }
  | {
      type: ControlMessageType.UHID_INPUT;
      id: number;
      data: Uint8Array;
    }
  | {
      type: ControlMessageType.UHID_DESTROY;
      id: number;
    }
  | {
      type: ControlMessageType.START_APP;
      name: string;
    }
  | {
      type: ControlMessageType.CAMERA_SET_TORCH;
      on: boolean;
    }
  | {
      type: ControlMessageType.RESIZE_DISPLAY;
      width: number;
      height: number;
    }
  | {
      type:
        | ControlMessageType.EXPAND_NOTIFICATION_PANEL
        | ControlMessageType.EXPAND_SETTINGS_PANEL
        | ControlMessageType.COLLAPSE_PANELS
        | ControlMessageType.ROTATE_DEVICE
        | ControlMessageType.OPEN_HARD_KEYBOARD_SETTINGS
        | ControlMessageType.RESET_VIDEO
        | ControlMessageType.CAMERA_ZOOM_IN
        | ControlMessageType.CAMERA_ZOOM_OUT;
    };
