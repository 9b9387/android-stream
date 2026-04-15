from __future__ import annotations

import argparse
import signal
import threading
import time
from pathlib import Path

from android_stream.sdk import AndroidStreamSDK, StreamConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start Android frame monitor service.")
    parser.add_argument("--device-serial", default=None, help="ADB device serial.")
    parser.add_argument("--fps", type=int, default=30, help="Target max fps.")
    parser.add_argument("--bitrate", type=int, default=8_000_000, help="Video bitrate.")
    parser.add_argument("--save-dir", default="frames", help="Directory to save frames.")
    parser.add_argument("--save-every", type=int, default=30, help="Save one jpeg every N frames.")
    parser.add_argument("--jpeg-quality", type=int, default=90, help="JPEG quality (1-100).")
    parser.add_argument(
        "--print-base64-length",
        action="store_true",
        help="Print current frame base64 length for LLM handoff diagnostics.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output_dir = Path(args.save_dir)
    stop_event = threading.Event()
    frame_count = 0

    sdk = AndroidStreamSDK(
        StreamConfig(
            device_serial=args.device_serial,
            max_fps=args.fps,
            bitrate=args.bitrate,
        )
    )

    def handle_frame(packet) -> None:
        nonlocal frame_count
        frame_count += 1
        message = f"frame={frame_count} ts={packet.timestamp_ms} size={packet.width}x{packet.height}"
        if args.print_base64_length:
            message += f" base64_len={len(packet.to_base64(quality=args.jpeg_quality))}"
        print(message)

        if args.save_every > 0 and frame_count % args.save_every == 0:
            file_path = output_dir / f"frame_{frame_count:06d}.jpg"
            packet.save_jpeg(file_path, quality=args.jpeg_quality)
            print(f"saved: {file_path}")

    def handle_error(exc: Exception) -> None:
        print(f"service error: {exc}")

    def shutdown(*_args) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    sdk.on_frame(handle_frame)
    sdk.on_error(handle_error)
    sdk.start()
    print("service started, press Ctrl+C to stop")

    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    finally:
        sdk.stop()
        print("service stopped")
