from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StreamState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(slots=True)
class StreamStats:
    frames_received: int = 0
    frames_dropped: int = 0
    errors: int = 0
