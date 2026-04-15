from __future__ import annotations

import signal
import threading
import time
from pathlib import Path

from android_stream.sdk import AndroidStreamSDK, StreamConfig


def main() -> None:
    sdk = AndroidStreamSDK(StreamConfig(max_fps=30, bitrate=8_000_000))
    stop_event = threading.Event()
    frame_count = 0
    out_dir = Path("frames")

    def on_frame(packet) -> None:
        nonlocal frame_count
        frame_count += 1
        print(f"frame={frame_count} size={packet.width}x{packet.height}")
        if frame_count % 30 == 0:
            path = out_dir / f"frame_{frame_count:06d}.jpg"
            packet.save_jpeg(path)
            print(f"saved: {path}")

    def on_error(exc: Exception) -> None:
        print(f"error: {exc}")

    def shutdown(*_args) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    sdk.on_frame(on_frame)
    sdk.on_error(on_error)
    sdk.start()
    print("sdk monitor started, press Ctrl+C to stop")

    try:
        while not stop_event.is_set():
            time.sleep(0.2)
    finally:
        sdk.stop()
        print("sdk monitor stopped")

if __name__ == "__main__":
    main()
