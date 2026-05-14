"""Async-friendly fan-out for the scrcpy v4 backend.

The backend itself runs on dedicated socket-reader threads. ``ScrcpyV4Service``
adapts that synchronous, single-consumer interface into an async pub/sub one
that any number of WebSocket connections can subscribe to.

Design notes:

- One scrcpy server process is shared across all browser clients. Each
  subscriber gets its own bounded :class:`asyncio.Queue`.
- For media packets we never block the producer thread on slow consumers; we
  drop the oldest *non-config* packets when a queue is full to keep the live
  view fresh. Config packets (codec extradata) are kept indefinitely so that
  late subscribers can still bootstrap their decoder.
- The first config packet of each stream and the latest video session packet
  are cached so that a newly-connected subscriber receives an immediate
  "init" snapshot.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable

from android_stream.exceptions import AndroidStreamError
from android_stream.models import StreamState

from .backend import ScrcpyV4Backend, StreamMeta
from .protocol import (
    CONTROL_MSG_TYPE,
    AudioCodec,
    ControlMessage,
    DeviceMessage,
    FrameMeta,
    SessionPacket,
    VideoCodec,
)

log = logging.getLogger(__name__)


class MediaKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    SESSION = "session"  # video session packets


@dataclass(slots=True, frozen=True)
class MediaPacket:
    kind: MediaKind
    pts_us: int
    config: bool
    key_frame: bool
    payload: bytes
    # Only set for SESSION packets.
    width: int = 0
    height: int = 0
    client_resized: bool = False


@dataclass(slots=True)
class ScrcpyV4Config:
    device_serial: str | None = None
    max_size: int = 0
    max_fps: int = 30
    video_bit_rate: int = 8_000_000
    audio_bit_rate: int = 128_000
    video_codec: VideoCodec = VideoCodec.H264
    audio_codec: AudioCodec = AudioCodec.OPUS
    audio: bool = True
    control: bool = True
    server_jar_path: str | None = None
    queue_max_packets: int = 240
    """How many packets to buffer per subscriber before dropping the oldest."""


@dataclass(slots=True)
class _Subscriber:
    queue: asyncio.Queue[MediaPacket | None]
    loop: asyncio.AbstractEventLoop
    drops: int = 0
    receive_audio: bool = True


class MediaSubscription:
    """Handle returned by :meth:`ScrcpyV4Service.subscribe`.

    Use ``async for packet in sub`` to consume packets, then call
    :meth:`close` when done. ``close()`` is idempotent.
    """

    def __init__(self, service: "ScrcpyV4Service", subscriber: _Subscriber) -> None:
        self._service = service
        self._subscriber = subscriber
        self._closed = False

    async def __aiter__(self):
        while True:
            packet = await self._subscriber.queue.get()
            if packet is None:
                return
            yield packet

    @property
    def drops(self) -> int:
        return self._subscriber.drops

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._service._remove_subscriber(self._subscriber)


class ScrcpyV4Service:
    """Manage one scrcpy v4 server connection and fan-out to subscribers."""

    def __init__(self, config: ScrcpyV4Config | None = None) -> None:
        self._config = config or ScrcpyV4Config()
        self._backend = ScrcpyV4Backend(
            device_serial=self._config.device_serial,
            max_size=self._config.max_size,
            max_fps=self._config.max_fps,
            video_bit_rate=self._config.video_bit_rate,
            audio_bit_rate=self._config.audio_bit_rate,
            video=True,
            audio=self._config.audio,
            control=self._config.control,
            video_codec=self._config.video_codec,
            audio_codec=self._config.audio_codec,
            server_jar_path=(
                __import__("pathlib").Path(self._config.server_jar_path)
                if self._config.server_jar_path
                else None
            ),
        )
        self._state = StreamState.STOPPED
        self._meta: StreamMeta | None = None
        self._latest_session: SessionPacket | None = None
        self._video_config: bytes | None = None
        self._audio_config: bytes | None = None
        # Cache the most recent video key frame (IDR / I-slice) so that a
        # late-joining subscriber can bootstrap its decoder without waiting
        # for the next periodic key frame — which on a static screen may
        # never come, and even when it does is up to ``i-frame-interval``
        # (default 10s) away. ``None`` until the first IDR has been seen.
        self._latest_key_frame: MediaPacket | None = None
        self._lock = threading.Lock()
        self._subscribers: list[_Subscriber] = []
        self._state_listeners: list[Callable[[StreamState], Awaitable[None] | None]] = []
        self._main_loop: asyncio.AbstractEventLoop | None = None

    # ----------------------------------------------------------- public API
    @property
    def state(self) -> StreamState:
        return self._state

    @property
    def meta(self) -> StreamMeta | None:
        return self._meta

    @property
    def config(self) -> ScrcpyV4Config:
        return self._config

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the asyncio loop used to schedule subscriber callbacks."""
        self._main_loop = loop

    def add_state_listener(
        self, listener: Callable[[StreamState], Awaitable[None] | None]
    ) -> Callable[[], None]:
        with self._lock:
            self._state_listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._state_listeners:
                    self._state_listeners.remove(listener)

        return unsubscribe

    def start(self) -> StreamMeta:
        if self._state in (StreamState.RUNNING, StreamState.STARTING):
            assert self._meta is not None
            return self._meta
        self._set_state(StreamState.STARTING)
        try:
            meta = self._backend.start(
                on_video=self._on_video,
                on_audio=self._on_audio,
                on_session=self._on_session,
                on_device_message=self._on_device_message,
                on_error=self._on_error,
            )
        except Exception:
            self._set_state(StreamState.ERROR)
            self._set_state(StreamState.STOPPED)
            raise
        self._meta = meta
        if meta.width and meta.height:
            self._latest_session = SessionPacket(
                width=meta.width, height=meta.height, client_resized=False
            )
        self._set_state(StreamState.RUNNING)
        return meta

    def stop(self) -> None:
        if self._state == StreamState.STOPPED:
            return
        self._set_state(StreamState.STOPPING)
        self._backend.stop()
        # Drop cached configs and notify subscribers so they tear down decoders.
        with self._lock:
            self._video_config = None
            self._audio_config = None
            self._latest_key_frame = None
            for sub in list(self._subscribers):
                self._enqueue(sub, None)
        self._set_state(StreamState.STOPPED)

    def send_control_message(self, msg: ControlMessage) -> None:
        self._backend.send_control_message(msg)

    def subscribe(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        receive_audio: bool = True,
    ) -> MediaSubscription:
        """Create a new subscriber bound to ``loop`` (defaults to running loop)."""
        target_loop = loop or asyncio.get_event_loop()
        queue: asyncio.Queue[MediaPacket | None] = asyncio.Queue(
            maxsize=self._config.queue_max_packets
        )
        subscriber = _Subscriber(queue=queue, loop=target_loop, receive_audio=receive_audio)
        with self._lock:
            self._subscribers.append(subscriber)
            initial = self._build_initial_snapshot_locked(receive_audio=receive_audio)
            need_key_frame = self._latest_key_frame is None
        for packet in initial:
            self._enqueue(subscriber, packet)
        # If we don't yet have a cached IDR, ask the encoder to emit one so
        # this subscriber can start decoding within milliseconds instead of
        # waiting for the next ``i-frame-interval`` (10s by default — and
        # even that only fires when the screen actually changes).
        if need_key_frame and self._config.control:
            try:
                self._backend.send_control_message(
                    ControlMessage.empty(CONTROL_MSG_TYPE.RESET_VIDEO)
                )
            except Exception as exc:  # pragma: no cover - best-effort
                log.debug("RESET_VIDEO request failed: %s", exc)
        return MediaSubscription(self, subscriber)

    # ---------------------------------------------------------- backend hooks
    def _on_video(self, meta: FrameMeta, payload: bytes) -> None:
        packet = MediaPacket(
            kind=MediaKind.VIDEO,
            pts_us=meta.pts_us,
            config=meta.config,
            key_frame=meta.key_frame,
            payload=payload,
        )
        with self._lock:
            if meta.config:
                self._video_config = payload
            elif meta.key_frame:
                # Hold on to the latest IDR so newcomers can bootstrap
                # without waiting for the next periodic key frame.
                self._latest_key_frame = packet
        self._broadcast(packet)

    def _on_audio(self, meta: FrameMeta, payload: bytes) -> None:
        packet = MediaPacket(
            kind=MediaKind.AUDIO,
            pts_us=meta.pts_us,
            config=meta.config,
            key_frame=False,
            payload=payload,
        )
        with self._lock:
            if meta.config:
                self._audio_config = payload
        self._broadcast(packet, audio_only=True)

    def _on_session(self, session: SessionPacket) -> None:
        packet = MediaPacket(
            kind=MediaKind.SESSION,
            pts_us=0,
            config=False,
            key_frame=False,
            payload=b"",
            width=session.width,
            height=session.height,
            client_resized=session.client_resized,
        )
        with self._lock:
            self._latest_session = session
        self._broadcast(packet)

    def _on_device_message(self, msg: DeviceMessage) -> None:
        # Reserved for clipboard / UHID forwarding. The minimal web client we
        # ship does not consume these yet, but logging them helps debugging
        # custom integrations that do.
        log.debug("scrcpy device message: %s", msg)

    def _on_error(self, exc: Exception) -> None:
        log.warning("scrcpy v4 backend error: %s", exc)
        self._set_state(StreamState.ERROR)
        with self._lock:
            for sub in list(self._subscribers):
                self._enqueue(sub, None)

    # ------------------------------------------------------------- helpers
    def _broadcast(self, packet: MediaPacket, *, audio_only: bool = False) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            if audio_only and not sub.receive_audio:
                continue
            self._enqueue(sub, packet)

    def _enqueue(self, subscriber: _Subscriber, packet: MediaPacket | None) -> None:
        loop = subscriber.loop
        if loop.is_closed():
            self._remove_subscriber(subscriber)
            return
        loop.call_soon_threadsafe(self._enqueue_in_loop, subscriber, packet)

    def _enqueue_in_loop(
        self, subscriber: _Subscriber, packet: MediaPacket | None
    ) -> None:
        queue = subscriber.queue
        if packet is None:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                # Make room for the close sentinel even if the queue is full.
                self._drain_one(queue)
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
            return
        try:
            queue.put_nowait(packet)
        except asyncio.QueueFull:
            # Drop oldest non-config packet to keep latency bounded.
            if not self._drop_one(queue):
                # The whole queue is config packets; drop the oldest anyway
                # rather than block the producer thread.
                self._drain_one(queue)
            subscriber.drops += 1
            try:
                queue.put_nowait(packet)
            except asyncio.QueueFull:
                subscriber.drops += 1

    @staticmethod
    def _drop_one(queue: asyncio.Queue[MediaPacket | None]) -> bool:
        # Walk the queue once, copying everything back except one droppable
        # packet (non-config, non-session). Falls back to dropping head if no
        # droppable item is present.
        items: list[MediaPacket | None] = []
        dropped = False
        while not queue.empty():
            try:
                items.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for index, candidate in enumerate(items):
            if (
                not dropped
                and candidate is not None
                and not candidate.config
                and candidate.kind != MediaKind.SESSION
            ):
                items.pop(index)
                dropped = True
                break
        for item in items:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                break
        return dropped

    @staticmethod
    def _drain_one(queue: asyncio.Queue[MediaPacket | None]) -> None:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

    def _build_initial_snapshot_locked(
        self, *, receive_audio: bool
    ) -> Iterable[MediaPacket]:
        snapshot: list[MediaPacket] = []
        if self._latest_session is not None:
            snapshot.append(
                MediaPacket(
                    kind=MediaKind.SESSION,
                    pts_us=0,
                    config=False,
                    key_frame=False,
                    payload=b"",
                    width=self._latest_session.width,
                    height=self._latest_session.height,
                    client_resized=False,
                )
            )
        if self._video_config is not None:
            snapshot.append(
                MediaPacket(
                    kind=MediaKind.VIDEO,
                    pts_us=0,
                    config=True,
                    key_frame=False,
                    payload=self._video_config,
                )
            )
        if self._latest_key_frame is not None:
            snapshot.append(self._latest_key_frame)
        if receive_audio and self._audio_config is not None:
            snapshot.append(
                MediaPacket(
                    kind=MediaKind.AUDIO,
                    pts_us=0,
                    config=True,
                    key_frame=False,
                    payload=self._audio_config,
                )
            )
        return snapshot

    def _remove_subscriber(self, subscriber: _Subscriber) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def _set_state(self, state: StreamState) -> None:
        if self._state == state:
            return
        self._state = state
        loop = self._main_loop
        if loop is None:
            return
        with self._lock:
            listeners = list(self._state_listeners)
        if not listeners:
            return

        def _emit() -> None:
            for listener in listeners:
                try:
                    result = listener(state)
                except Exception:
                    log.exception("state listener raised")
                    continue
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)

        loop.call_soon_threadsafe(_emit)
