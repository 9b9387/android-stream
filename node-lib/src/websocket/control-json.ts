import {
  ACTION_DOWN,
  ACTION_MOVE,
  ACTION_UP,
  BUTTON_PRIMARY,
  ControlMessage,
  ControlMessageType,
  KEY_ACTION_DOWN,
  KEY_ACTION_UP,
  POINTER_ID_GENERIC_FINGER,
  POINTER_ID_MOUSE,
} from "../protocol/index.js";

export function parseControlJson(payload: any): ControlMessage | null {
  const type = (payload.type || "").toLowerCase();

  switch (type) {
    case "touch": {
      const action = parseTouchAction(payload.action);
      return {
        type: ControlMessageType.INJECT_TOUCH_EVENT,
        action,
        pointerId: parsePointerId(payload.pointerId),
        x: payload.x,
        y: payload.y,
        screenWidth: payload.screenWidth,
        screenHeight: payload.screenHeight,
        pressure: payload.pressure ?? (action !== ACTION_UP ? 1.0 : 0.0),
        actionButton:
          payload.actionButton ??
          (action === ACTION_DOWN || action === ACTION_UP ? BUTTON_PRIMARY : 0),
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
        hScroll: payload.hScroll ?? 0.0,
        vScroll: payload.vScroll ?? 0.0,
        buttons: payload.buttons ?? 0,
      };
    case "key":
      return {
        type: ControlMessageType.INJECT_KEYCODE,
        action: parseKeyAction(payload.action),
        keycode: payload.keycode,
        repeat: payload.repeat ?? 0,
        metaState: payload.metaState ?? 0,
      };
    case "text":
      return { type: ControlMessageType.INJECT_TEXT, text: payload.text ?? "" };
    case "back":
      return {
        type: ControlMessageType.BACK_OR_SCREEN_ON,
        action: parseKeyAction(payload.action),
      };
    case "home":
      return {
        type: ControlMessageType.INJECT_KEYCODE,
        action: parseKeyAction(payload.action),
        keycode: 3,
      };
    case "app_switch":
      return {
        type: ControlMessageType.INJECT_KEYCODE,
        action: parseKeyAction(payload.action),
        keycode: 187,
      };
    case "power":
      return {
        type: ControlMessageType.INJECT_KEYCODE,
        action: parseKeyAction(payload.action),
        keycode: 26,
      };
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
        sequence: BigInt(payload.sequence ?? 0),
        text: payload.text ?? "",
        paste: !!payload.paste,
      };
    case "get_clipboard":
      return {
        type: ControlMessageType.GET_CLIPBOARD,
        copyKey: payload.copyKey ?? 0,
      };
    case "set_display_power":
      return { type: ControlMessageType.SET_DISPLAY_POWER, on: !!payload.on };
    case "start_app":
      return { type: ControlMessageType.START_APP, name: payload.name ?? "" };
    default:
      return null;
  }
}

function parseTouchAction(value: any): number {
  if (typeof value === "number") return value;
  const text = String(value).toLowerCase();
  if (text === "down" || text === "pointer_down") return ACTION_DOWN;
  if (text === "up" || text === "pointer_up") return ACTION_UP;
  if (text === "move") return ACTION_MOVE;
  throw new Error(`unknown touch action: ${value}`);
}

function parseKeyAction(value: any): number {
  if (typeof value === "number") return value;
  const text = String(value).toLowerCase();
  if (text === "down") return KEY_ACTION_DOWN;
  if (text === "up") return KEY_ACTION_UP;
  throw new Error(`unknown key action: ${value}`);
}

function parsePointerId(value: any): bigint {
  if (value === null || value === undefined) return POINTER_ID_GENERIC_FINGER;
  if (typeof value === "string") {
    const text = value.toLowerCase();
    if (text === "mouse") return POINTER_ID_MOUSE;
    if (text === "finger") return POINTER_ID_GENERIC_FINGER;
    return BigInt(text);
  }
  return BigInt(value);
}
