"""Framework-independent immutable NexLinq daemon state snapshots."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class DaemonSnapshot:
    """Public daemon state safe to share across threads."""

    lifecycle_state: str = "stopped"
    device_available: bool = False
    device_path: str | None = None
    multiple_device_warning: str | None = None
    rendering_state: str = "idle"
    active_configuration_revision: int = 0
    last_frame_generation_time: float | None = None
    last_successful_upload_time: float | None = None
    last_display_error: str | None = None


class DaemonState:
    """Short-lock state store returning immutable snapshots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = DaemonSnapshot()

    def snapshot(self) -> DaemonSnapshot:
        with self._lock:
            return self._snapshot

    def update(self, **changes: object) -> DaemonSnapshot:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)
            return self._snapshot
