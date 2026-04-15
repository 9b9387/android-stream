from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from android_stream.backends.base import FrameBackend
from android_stream.backends.scrcpy_proto_backend import ScrcpyProtoBackend
from android_stream.frame_types import FramePacket
from android_stream.models import StreamState, StreamStats

OnFrameCallback = Callable[[FramePacket], None]
OnErrorCallback = Callable[[Exception], None]
OnStateCallback = Callable[[StreamState], None]


class AndroidFrameService:
    def __init__(
        self,
        *,
        device_serial: str | None = None,
        max_fps: int = 30,
        bitrate: int = 8_000_000,
    ) -> None:
        self._backend = ScrcpyProtoBackend(
            device_serial=device_serial,
            max_fps=max_fps,
            bitrate=bitrate,
        )
        self._frame_callbacks: list[OnFrameCallback] = []
        self._error_callbacks: list[OnErrorCallback] = []
        self._state_callbacks: list[OnStateCallback] = []
        self._state = StreamState.STOPPED
        self._running = False
        self._stats = StreamStats()
        self._lock = threading.Lock()

        # 保留最新帧策略：满队列时丢弃旧帧并放入新帧，避免高负载下累计延迟。
        self._frame_queue: queue.Queue[FramePacket] = queue.Queue(maxsize=1)
        self._worker_thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._state == StreamState.RUNNING

    @property
    def state(self) -> StreamState:
        return self._state

    @property
    def stats(self) -> StreamStats:
        with self._lock:
            return StreamStats(
                frames_received=self._stats.frames_received,
                frames_dropped=self._stats.frames_dropped,
                errors=self._stats.errors,
            )

    def on_frame(self, callback: OnFrameCallback) -> Callable[[], None]:
        with self._lock:
            self._frame_callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._frame_callbacks:
                    self._frame_callbacks.remove(callback)

        return unsubscribe

    def on_error(self, callback: OnErrorCallback) -> Callable[[], None]:
        with self._lock:
            self._error_callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._error_callbacks:
                    self._error_callbacks.remove(callback)

        return unsubscribe

    def on_state_change(self, callback: OnStateCallback) -> Callable[[], None]:
        with self._lock:
            self._state_callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._state_callbacks:
                    self._state_callbacks.remove(callback)

        return unsubscribe

    def start(self) -> None:
        state_callbacks: list[OnStateCallback] = []
        with self._lock:
            if self._running:
                return
            self._running = True
            state_callbacks = self._set_state_locked(StreamState.STARTING)
            self._worker_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
            self._worker_thread.start()
        self._emit_state_callbacks(state_callbacks, StreamState.STARTING)

        try:
            self._backend.start(frame_handler=self._enqueue_frame, error_handler=self._handle_backend_error)
            with self._lock:
                state_callbacks = self._set_state_locked(StreamState.RUNNING)
            self._emit_state_callbacks(state_callbacks, StreamState.RUNNING)
        except Exception:
            # 若 backend 启动失败，回滚 service 运行状态，避免出现“半启动”。
            error_callbacks: list[OnStateCallback] = []
            stopped_callbacks: list[OnStateCallback] = []
            with self._lock:
                self._running = False
                error_callbacks = self._set_state_locked(StreamState.ERROR)
            if self._worker_thread is not None:
                self._worker_thread.join(timeout=1.0)
                self._worker_thread = None
            with self._lock:
                stopped_callbacks = self._set_state_locked(StreamState.STOPPED)
            self._emit_state_callbacks(error_callbacks, StreamState.ERROR)
            self._emit_state_callbacks(stopped_callbacks, StreamState.STOPPED)
            raise

    def stop(self) -> None:
        stopping_callbacks: list[OnStateCallback] = []
        should_stop_backend = False
        with self._lock:
            if not self._running and self._state == StreamState.STOPPED:
                return
            should_stop_backend = True
            self._running = False
            stopping_callbacks = self._set_state_locked(StreamState.STOPPING)
        self._emit_state_callbacks(stopping_callbacks, StreamState.STOPPING)

        if should_stop_backend:
            self._backend.stop()

        if self._worker_thread is not None:
            self._worker_thread.join(timeout=1.0)
            self._worker_thread = None
        with self._lock:
            stopped_callbacks = self._set_state_locked(StreamState.STOPPED)
        self._emit_state_callbacks(stopped_callbacks, StreamState.STOPPED)

    def _enqueue_frame(self, packet: FramePacket) -> None:
        if not self._running:
            return
        with self._lock:
            self._stats.frames_received += 1
        if self._frame_queue.full():
            try:
                self._frame_queue.get_nowait()
                with self._lock:
                    self._stats.frames_dropped += 1
            except queue.Empty:
                pass
        try:
            self._frame_queue.put_nowait(packet)
        except queue.Full:
            pass

    def _dispatch_loop(self) -> None:
        while self._running:
            try:
                packet = self._frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            callbacks = self._snapshot_callbacks(self._frame_callbacks)
            for callback in callbacks:
                try:
                    callback(packet)
                except Exception as exc:
                    self._handle_error(exc)

    def _snapshot_callbacks(self, callbacks: list[Callable[[Any], None]]) -> list[Callable[[Any], None]]:
        with self._lock:
            return list(callbacks)

    def _handle_error(self, exc: Exception) -> None:
        with self._lock:
            self._stats.errors += 1
        callbacks = self._snapshot_callbacks(self._error_callbacks)
        if not callbacks:
            return
        for callback in callbacks:
            try:
                callback(exc)
            except Exception:
                # 错误回调不应再抛出异常打断主流程。
                continue

    def _handle_backend_error(self, exc: Exception) -> None:
        state_callbacks: list[OnStateCallback] = []
        with self._lock:
            self._running = False
            state_callbacks = self._set_state_locked(StreamState.ERROR)
        self._emit_state_callbacks(state_callbacks, StreamState.ERROR)
        self._handle_error(exc)

    def _set_state_locked(self, new_state: StreamState) -> list[OnStateCallback]:
        if self._state == new_state:
            return []
        self._state = new_state
        return list(self._state_callbacks)

    @staticmethod
    def _emit_state_callbacks(callbacks: list[OnStateCallback], state: StreamState) -> None:
        for callback in callbacks:
            try:
                callback(state)
            except Exception:
                continue

    def _set_backend_for_test(self, backend: FrameBackend) -> None:
        if self._running:
            raise RuntimeError("cannot swap backend while service is running")
        self._backend = backend
