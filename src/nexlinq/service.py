"""Framework-independent NexLinq service orchestration."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nexlinq.config_store import ConfigSaveError, ConfigStore
from nexlinq.defaults import default_configuration
from nexlinq.device_worker import DeviceWorker, DeviceWorkerSnapshot
from nexlinq.models import ConfigurationDocument, ProfileConfig
from nexlinq.rendering import physical_preview_bytes, render_profile_at_elapsed
from nexlinq.state import DaemonState, DaemonSnapshot

AsyncSleep = Callable[[float], Awaitable[None]]
WallClock = Callable[[], datetime]
TaskFactory = Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]]
MutationGate = Callable[[str, int], Awaitable[None]]


class RevisionConflictError(RuntimeError):
    """Raised when a mutation targets a stale runtime revision."""


class ServiceUnavailableError(RuntimeError):
    """Raised when runtime mutations are no longer accepted."""


class WorkerSubmissionError(RuntimeError):
    """Raised when a candidate could not be submitted consistently."""


@dataclass(frozen=True, slots=True)
class ServiceConfigurationSnapshot:
    configuration: ConfigurationDocument
    built_in_default: ConfigurationDocument
    active_revision: int
    saved_revision: int | None
    dirty: bool


@dataclass(frozen=True, slots=True)
class ApplyResult:
    profile_name: str
    profile: ProfileConfig
    active_revision: int
    dirty: bool


@dataclass(frozen=True, slots=True)
class SaveResult:
    configuration: ConfigurationDocument
    active_revision: int
    saved_revision: int | None
    dirty: bool
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class PreviewSnapshot:
    framebuffer: bytes
    active_revision: int
    generated_at: float
    profile: ProfileConfig


def _default_task_factory(
    coroutine: Coroutine[Any, Any, None],
) -> asyncio.Task[None]:
    return asyncio.create_task(coroutine, name="nexlinq-render-loop")


async def _open_mutation_gate(kind: str, revision: int) -> None:
    return None


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
        mutation_gate: MutationGate = _open_mutation_gate,
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
        self._mutation_gate = mutation_gate
        self._configuration: ConfigurationDocument | None = None
        self._render_task: asyncio.Task[None] | None = None
        self._started_at = 0.0
        self._last_worker_uploads = 0
        self._worker_started = False
        self._saved_configuration: ConfigurationDocument | None = None
        self._saved_revision: int | None = None
        self._saved_revision_counter = 0
        self._mutation_lock = asyncio.Lock()
        self._coordination_lock = threading.RLock()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._worker_failure_event: asyncio.Event | None = None
        self._worker_exit_observed = threading.Event()

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
            self._event_loop = asyncio.get_running_loop()
            self._worker_failure_event = asyncio.Event()
            self._worker_exit_observed.clear()
            loaded = self.config_store.load()
            with self._coordination_lock:
                self._configuration = loaded.configuration
                self._saved_configuration = (
                    loaded.configuration if loaded.persisted else None
                )
                self._saved_revision = 1 if loaded.persisted else None
                self._saved_revision_counter = self._saved_revision or 0
                self.state.update(
                    active_configuration_revision=1,
                    configuration_warning=(
                        "Configuration could not be loaded; built-in defaults "
                        "are active."
                        if loaded.warning
                        else None
                    ),
                )
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
            with self._coordination_lock:
                worker_snapshot = self.device_worker.snapshot()
                if (
                    not worker_snapshot.running
                    or self.snapshot().lifecycle_state != "starting"
                ):
                    raise RuntimeError(
                        "The Matrix900 device worker stopped during startup."
                    )
                self.state.update(
                    lifecycle_state="running",
                    rendering_state="rendering",
                )
                if self._worker_exit_observed.is_set():
                    raise RuntimeError(
                        "The Matrix900 device worker stopped during startup."
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

    async def wait_for_worker_failure(self) -> None:
        """Wait until the owned worker exits unexpectedly."""
        event = self._worker_failure_event
        if event is None:
            raise RuntimeError("NexLinqService has not been started.")
        await event.wait()

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
            profile, revision = self._capture_active_profile()
            frame = render_profile_at_elapsed(
                profile,
                self._wall_clock(),
                elapsed,
            )
            self.device_worker.submit_frame(
                frame.to_bytes(),
                revision=revision,
            )
            self.state.update(last_frame_generation_time=generated_at)
            next_frame += interval
            await self._sleep(max(0.0, next_frame - self._monotonic()))

    def get_configuration(self) -> ServiceConfigurationSnapshot:
        """Return immutable runtime and persistence configuration state."""
        with self._coordination_lock:
            configuration = self.configuration
            return ServiceConfigurationSnapshot(
                configuration=configuration,
                built_in_default=default_configuration(),
                active_revision=self.snapshot().active_configuration_revision,
                saved_revision=self._saved_revision,
                dirty=self._saved_configuration != configuration,
            )

    async def apply_profile(
        self,
        profile_name: str,
        profile: ProfileConfig,
        *,
        expected_revision: int | None = None,
    ) -> ApplyResult:
        """Validate, candidate-render, and publish one runtime-only profile."""
        async with self._mutation_lock:
            with self._coordination_lock:
                self._require_running()
                current_revision = self.snapshot().active_configuration_revision
                self._check_revision(expected_revision, current_revision)
                current = self.configuration
            profiles = dict(current.profiles)
            profiles[profile_name] = profile
            candidate = ConfigurationDocument(
                schema_version=current.schema_version,
                active_profile=profile_name,
                profiles=profiles,
            )
            frame = self._render_candidate(candidate.active)
            new_revision = current_revision + 1
            await self._mutation_gate("apply", new_revision)
            with self._coordination_lock:
                self._require_running()
                self._submit_candidate(
                    frame,
                    profile.brightness,
                    revision=new_revision,
                )
                self._configuration = candidate
                self.state.update(active_configuration_revision=new_revision)
                dirty = self._saved_configuration != candidate
        return ApplyResult(
            profile_name=profile_name,
            profile=profile,
            active_revision=new_revision,
            dirty=dirty,
        )

    def _submit_candidate(
        self,
        frame: bytes,
        brightness: int | None,
        *,
        revision: int,
    ) -> None:
        frame_accepted = False
        try:
            self.device_worker.submit_frame(frame, revision=revision)
            frame_accepted = True
            if brightness is not None:
                self.device_worker.set_brightness(
                    brightness,
                    revision=revision,
                )
        except Exception as error:
            if frame_accepted:
                self.state.update(
                    lifecycle_state="failed",
                    rendering_state="failed",
                    last_display_error=(
                        "Matrix900 command submission failed after a frame "
                        "was accepted."
                    ),
                )
                if self._worker_failure_event is not None:
                    self._worker_failure_event.set()
            raise WorkerSubmissionError(
                "Matrix900 command submission failed."
            ) from error

    async def save_configuration(
        self,
        configuration: ConfigurationDocument,
        *,
        expected_active_revision: int | None = None,
        expected_saved_revision: int | None = None,
        apply: bool = True,
    ) -> SaveResult:
        """Atomically save a document and optionally publish it at runtime."""
        async with self._mutation_lock:
            with self._coordination_lock:
                self._require_running()
                current_revision = self.snapshot().active_configuration_revision
                current_saved_revision = self._saved_revision
                if apply:
                    self._check_revision(
                        expected_active_revision,
                        current_revision,
                    )
                self._check_revision(
                    expected_saved_revision,
                    current_saved_revision,
                )
            frame = self._render_candidate(configuration.active) if apply else None
            warning: str | None = None
            try:
                self.config_store.save(configuration)
            except ConfigSaveError as error:
                if not error.committed:
                    raise
                warning = (
                    "Configuration was saved, but directory durability "
                    "could not be confirmed."
                )
            await self._mutation_gate(
                "save-apply" if apply else "save",
                current_revision + int(apply),
            )
            with self._coordination_lock:
                self._saved_configuration = configuration
                self._saved_revision_counter += 1
                self._saved_revision = self._saved_revision_counter
                self.state.update(configuration_warning=warning)
                if apply:
                    self._require_running()
                    new_revision = current_revision + 1
                    assert frame is not None
                    self._submit_candidate(
                        frame,
                        configuration.active.brightness,
                        revision=new_revision,
                    )
                    self._configuration = configuration
                    self.state.update(active_configuration_revision=new_revision)
                else:
                    new_revision = current_revision
                saved_revision = self._saved_revision
                dirty = self._saved_configuration != self.configuration
        return SaveResult(
            configuration=configuration,
            active_revision=new_revision,
            saved_revision=saved_revision,
            dirty=dirty,
            warning=warning,
        )

    def get_preview(self) -> PreviewSnapshot:
        """Render the current runtime profile using the physical renderer."""
        generated_at = self._monotonic()
        profile, revision = self._capture_active_profile()
        frame = render_profile_at_elapsed(
            profile,
            self._wall_clock(),
            max(0.0, generated_at - self._started_at),
        )
        return PreviewSnapshot(
            framebuffer=physical_preview_bytes(frame, profile),
            active_revision=revision,
            generated_at=generated_at,
            profile=profile,
        )

    def _render_candidate(self, profile: ProfileConfig) -> bytes:
        generated_at = self._monotonic()
        return render_profile_at_elapsed(
            profile,
            self._wall_clock(),
            max(0.0, generated_at - self._started_at),
        ).to_bytes()

    def _capture_active_profile(self) -> tuple[ProfileConfig, int]:
        with self._coordination_lock:
            return (
                self.configuration.active,
                self.snapshot().active_configuration_revision,
            )

    @staticmethod
    def _check_revision(expected: int | None, current: int | None) -> None:
        if expected is not None and expected != current:
            raise RevisionConflictError(
                "The configuration revision has changed."
            )

    def _require_running(self) -> None:
        if self.snapshot().lifecycle_state != "running":
            raise ServiceUnavailableError(
                "The NexLinq service is not accepting configuration changes."
            )

    def handle_worker_snapshot(self, snapshot: DeviceWorkerSnapshot) -> None:
        """Copy sanitized device status into daemon state."""
        if not snapshot.running:
            self._worker_exit_observed.set()
        with self._coordination_lock:
            changes: dict[str, object] = {
                "device_available": snapshot.device_available,
                "device_path": snapshot.device_path,
                "multiple_device_warning": snapshot.multiple_device_warning,
                "last_display_error": snapshot.last_display_error,
            }
            if snapshot.successful_uploads > self._last_worker_uploads:
                self._last_worker_uploads = snapshot.successful_uploads
                changes["last_successful_upload_time"] = self._monotonic()
            lifecycle = self.snapshot().lifecycle_state
            if not snapshot.running and lifecycle in ("starting", "running"):
                changes.update(
                    lifecycle_state="failed",
                    rendering_state="failed",
                    last_display_error=(
                        "Matrix900 device worker stopped unexpectedly."
                    ),
                )
            self.state.update(**changes)
            event_loop = self._event_loop
            failure_event = self._worker_failure_event
        if (
            not snapshot.running
            and lifecycle in ("starting", "running")
            and event_loop is not None
            and failure_event is not None
        ):
            event_loop.call_soon_threadsafe(failure_event.set)
