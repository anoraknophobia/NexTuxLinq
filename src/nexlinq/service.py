"""Framework-independent NexLinq service orchestration."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
from typing import Any

from nexlinq.config_store import ConfigStore
from nexlinq.device_worker import DeviceWorker, DeviceWorkerSnapshot
from nexlinq.models import ConfigurationDocument
from nexlinq.rendering import render_profile_at_elapsed
from nexlinq.state import DaemonState, DaemonSnapshot

AsyncSleep = Callable[[float], Awaitable[None]]
WallClock = Callable[[], datetime]
TaskFactory = Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]]


def _default_task_factory(
    coroutine: Coroutine[Any, Any, None],
) -> asyncio.Task[None]:
    return asyncio.create_task(coroutine, name="nexlinq-render-loop")


class NexLinqService:
    """Load configuration, render frames, and feed the single device worker."""

    def __init__(
        self,
        *,
        config_store: ConfigStore,
        device_worker: DeviceWorker,
        state: DaemonState | None = None,
        frame_rate: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: WallClock = datetime.now,
        sleep: AsyncSleep = asyncio.sleep,
        worker_stop_timeout: float | None = 5.0,
        task_factory: TaskFactory = _default_task_factory,
    ) -> None:
        if frame_rate <= 0:
            raise ValueError("frame_rate must be positive.")
        self.config_store = config_store
        self.device_worker = device_worker
        self.state = DaemonState() if state is None else state
        self.frame_rate = frame_rate
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._worker_stop_timeout = worker_stop_timeout
        self._task_factory = task_factory
        self._configuration: ConfigurationDocument | None = None
        self._render_task: asyncio.Task[None] | None = None
        self._started_at = 0.0
        self._last_worker_uploads = 0
        self._worker_started = False

    @property
    def configuration(self) -> ConfigurationDocument:
        if self._configuration is None:
            raise RuntimeError("NexLinqService has not been started.")
        return self._configuration

    def snapshot(self) -> DaemonSnapshot:
        return self.state.snapshot()

    @property
    def render_task(self) -> asyncio.Task[None] | None:
        """Return the active render task for foreground-process monitoring."""
        return self._render_task

    @property
    def owns_resources(self) -> bool:
        """Return whether startup acquired resources that require cleanup."""
        return self._worker_started or self._render_task is not None

    async def start(self) -> None:
        if self.owns_resources:
            raise RuntimeError("NexLinqService is already started.")
        self.state.update(lifecycle_state="starting", rendering_state="idle")
        try:
            loaded = self.config_store.load()
            self._configuration = loaded.configuration
            self.state.update(active_configuration_revision=1)
            self.device_worker.set_status_callback(self.handle_worker_snapshot)
            self.device_worker.start()
            self._worker_started = True
            profile = self.configuration.active
            if profile.brightness is not None:
                self.device_worker.set_brightness(profile.brightness, revision=1)
            self._started_at = self._monotonic()
            render_coroutine = self._monitored_render_loop()
            try:
                self._render_task = self._task_factory(render_coroutine)
            except BaseException:
                render_coroutine.close()
                raise
            self.state.update(
                lifecycle_state="running",
                rendering_state="rendering",
            )
        except BaseException:
            try:
                await self._cleanup_resources()
            except Exception:
                pass
            try:
                self.state.update(
                    lifecycle_state="failed",
                    rendering_state="failed",
                    last_display_error="NexLinq service startup failed.",
                )
            except Exception:
                pass
            raise

    async def stop(self) -> None:
        self.state.update(lifecycle_state="stopping", rendering_state="idle")
        try:
            await self._cleanup_resources()
        except TimeoutError:
            self.state.update(
                lifecycle_state="failed",
                rendering_state="failed",
                last_display_error="Matrix900 worker shutdown timed out.",
            )
            raise
        self.state.update(
            lifecycle_state="stopped",
            rendering_state="idle",
            device_available=False,
            device_path=None,
        )

    async def _cleanup_resources(self) -> None:
        task = self._render_task
        self._render_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self._worker_started:
            self.device_worker.stop(timeout=self._worker_stop_timeout)
            self._worker_started = False

    async def _monitored_render_loop(self) -> None:
        try:
            await self._render_loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.state.update(
                lifecycle_state="failed",
                rendering_state="failed",
                last_display_error="Matrix900 rendering failed.",
            )
            raise

    async def _render_loop(self) -> None:
        interval = 1.0 / self.frame_rate
        next_frame = self._monotonic()
        while True:
            generated_at = self._monotonic()
            elapsed = generated_at - self._started_at
            frame = render_profile_at_elapsed(
                self.configuration.active,
                self._wall_clock(),
                elapsed,
            )
            self.device_worker.submit_frame(
                frame.to_bytes(),
                revision=self.state.snapshot().active_configuration_revision,
            )
            self.state.update(last_frame_generation_time=generated_at)
            next_frame += interval
            await self._sleep(max(0.0, next_frame - self._monotonic()))

    def handle_worker_snapshot(self, snapshot: DeviceWorkerSnapshot) -> None:
        """Copy sanitized device status into daemon state."""
        changes: dict[str, object] = {
            "device_available": snapshot.device_available,
            "device_path": snapshot.device_path,
            "multiple_device_warning": snapshot.multiple_device_warning,
            "last_display_error": snapshot.last_display_error,
        }
        if snapshot.successful_uploads > self._last_worker_uploads:
            self._last_worker_uploads = snapshot.successful_uploads
            changes["last_successful_upload_time"] = self._monotonic()
        self.state.update(**changes)
