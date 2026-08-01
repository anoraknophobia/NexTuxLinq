"""Framework-independent canonical schema-v2 service orchestration."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nextuxlinq.config_store import (
    ConfigSaveError,
    ConfigStore,
    ManagedBackup,
    ManagedBackupError,
)
from nextuxlinq.defaults import (
    default_configuration,
    legacy_equivalent_configuration_v2,
)
from nextuxlinq.device_worker import DeviceWorker, DeviceWorkerSnapshot
from nextuxlinq.legacy_compat import (
    LegacyEditorUnsupportedError,
    apply_legacy_configuration,
    apply_legacy_profile,
    legacy_configuration_from_v2,
)
from nextuxlinq.migration import migrate_configuration_v1_to_v2
from nextuxlinq.models import ConfigurationDocument, ProfileConfig
from nextuxlinq.models_v2 import (
    ConfigurationDocumentV2,
    ProfileConfigV2,
)
from nextuxlinq.rendering_v2.compositor import (
    Compositor,
    CompositorError,
    RenderResult,
)
from nextuxlinq.rendering_v2.registry import create_widget_registry
from nextuxlinq.rendering_v2.update_policy import UpdatePolicy
from nextuxlinq.scheduler import update_policy_delay
from nextuxlinq.state import DaemonState, DaemonSnapshot
from nextuxlinq.system_metrics import (
    LinuxSystemMetricsProvider,
    MetricAvailability,
    MetricId,
    SystemMetricsSnapshot,
)
from nextuxlinq.widget_models import WidgetType

AsyncSleep = Callable[[float], Awaitable[None]]
WallClock = Callable[[], datetime]
TaskFactory = Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]]
MutationGate = Callable[[str, int], Awaitable[None]]


class RevisionConflictError(RuntimeError):
    """Raised when a mutation targets a stale runtime revision."""


class ServiceUnavailableError(RuntimeError):
    """Raised when runtime mutations are no longer accepted."""


class WorkerSubmissionError(RuntimeError):
    """A sanitized candidate submission failure."""


class CandidateValidationError(ValueError):
    """A sanitized complete-candidate rendering failure."""

    def __init__(
        self,
        category: str,
        *,
        region_id: str | None = None,
        widget_type: WidgetType | None = None,
        field: str | None = None,
    ) -> None:
        self.category = category
        self.region_id = region_id
        self.widget_type = widget_type
        self.field = field
        super().__init__("The free-layout candidate could not be rendered.")


@dataclass(frozen=True, slots=True)
class ServiceConfigurationSnapshot:
    configuration: ConfigurationDocumentV2
    built_in_default: ConfigurationDocumentV2
    active_revision: int
    saved_revision: int | None
    dirty: bool
    migration_pending: bool
    legacy_configuration: ConfigurationDocument | None
    legacy_built_in_default: ConfigurationDocument
    legacy_editor_supported: bool


@dataclass(frozen=True, slots=True)
class ApplyResult:
    profile_name: str
    profile: ProfileConfig
    active_revision: int
    dirty: bool


@dataclass(frozen=True, slots=True)
class CanonicalApplyResult:
    configuration: ConfigurationDocumentV2
    render_result: RenderResult
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
class CanonicalSaveResult:
    configuration: ConfigurationDocumentV2
    active_revision: int
    saved_revision: int | None
    dirty: bool
    migration_pending: bool
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class PreviewSnapshot:
    framebuffer: bytes
    device_framebuffer: bytes
    active_revision: int
    generated_at: float
    profile: ProfileConfigV2
    render_result: RenderResult


def _default_task_factory(
    coroutine: Coroutine[Any, Any, None],
) -> asyncio.Task[None]:
    return asyncio.create_task(coroutine, name="nextuxlinq-render-loop")


async def _open_mutation_gate(kind: str, revision: int) -> None:
    return None


def _local_wall_clock() -> datetime:
    return datetime.now().astimezone()


def _canonical_configuration(
    configuration: ConfigurationDocumentV2 | ConfigurationDocument,
) -> ConfigurationDocumentV2:
    if type(configuration) is ConfigurationDocumentV2:
        return configuration
    if type(configuration) is ConfigurationDocument:
        return migrate_configuration_v1_to_v2(configuration)
    raise TypeError("configuration must be a validated configuration.")


class NexTuxLinqService:
    """Render canonical profiles and feed the single existing device worker."""

    def __init__(
        self,
        *,
        config_store: ConfigStore,
        device_worker: DeviceWorker,
        state: DaemonState | None = None,
        frame_rate: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: WallClock = _local_wall_clock,
        sleep: AsyncSleep = asyncio.sleep,
        worker_stop_timeout: float | None = 5.0,
        task_factory: TaskFactory = _default_task_factory,
        mutation_gate: MutationGate = _open_mutation_gate,
        metrics_provider: Any | None = None,
    ) -> None:
        if frame_rate <= 0:
            raise ValueError("frame_rate must be positive.")
        self.config_store = config_store
        self.device_worker = device_worker
        self.state = DaemonState() if state is None else state
        self.frame_rate = float(frame_rate)
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._worker_stop_timeout = worker_stop_timeout
        self._task_factory = task_factory
        self._mutation_gate = mutation_gate
        self._metrics_provider = (
            LinuxSystemMetricsProvider(
                monotonic=monotonic,
                wall_clock=wall_clock,
            )
            if metrics_provider is None
            else metrics_provider
        )
        self._last_metrics_snapshot: SystemMetricsSnapshot | None = None
        self._widget_registry = create_widget_registry()
        self._compositor = Compositor(self._widget_registry)
        self._configuration: ConfigurationDocumentV2 | None = None
        self._render_task: asyncio.Task[None] | None = None
        self._render_wakeup: asyncio.Event | None = None
        self._started_at = 0.0
        self._last_worker_uploads = 0
        self._worker_started = False
        self._saved_configuration: ConfigurationDocumentV2 | None = None
        self._saved_revision: int | None = None
        self._saved_revision_counter = 0
        self._migration_pending = False
        self._last_render_result: RenderResult | None = None
        self._last_render_revision: int | None = None
        self._mutation_lock = asyncio.Lock()
        self._coordination_lock = threading.RLock()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._worker_failure_event: asyncio.Event | None = None
        self._worker_exit_observed = threading.Event()

    @property
    def configuration(self) -> ConfigurationDocumentV2:
        if self._configuration is None:
            raise RuntimeError("NexTuxLinqService has not been started.")
        if type(self._configuration) is not ConfigurationDocumentV2:
            self._configuration = _canonical_configuration(
                self._configuration
            )
        if (
            self._saved_configuration is not None
            and type(self._saved_configuration) is not ConfigurationDocumentV2
        ):
            self._saved_configuration = _canonical_configuration(
                self._saved_configuration
            )
        return self._configuration

    def snapshot(self) -> DaemonSnapshot:
        return self.state.snapshot()

    def get_widget_definitions(self) -> tuple[Any, ...]:
        """Return immutable canonical definitions for editor metadata."""
        return self._widget_registry.definitions

    def get_metric_status(self) -> dict[str, str]:
        """Return sanitized availability without triggering a sample."""
        snapshot = self._last_metrics_snapshot
        if snapshot is None:
            return {
                metric_id.value: MetricAvailability.UNAVAILABLE.value
                for metric_id in MetricId
            }
        return {
            metric_id.value: snapshot.availability[metric_id].value
            for metric_id in MetricId
        }

    def get_metric_diagnostics(self) -> dict[str, tuple[str, str | None]]:
        """Return normalized, path-free availability and reason categories."""
        snapshot = self._last_metrics_snapshot
        if snapshot is None:
            return {
                metric_id.value: ("unavailable", "source_not_present")
                for metric_id in MetricId
            }
        diagnostics: dict[str, tuple[str, str | None]] = {}
        for metric_id in MetricId:
            availability = snapshot.availability[metric_id]
            state = (
                "available"
                if availability is MetricAvailability.AVAILABLE
                else "awaiting_initial_delta"
                if availability in {
                    MetricAvailability.AWAITING_INITIAL_CPU_DELTA,
                    MetricAvailability.AWAITING_INITIAL_DELTA,
                }
                else "unavailable"
            )
            reason = snapshot.reasons[metric_id]
            diagnostics[metric_id.value] = (
                state,
                None if reason is None else reason.value,
            )
        return diagnostics

    def list_backups(self) -> tuple[ManagedBackup, ...]:
        """Return managed backup metadata without exposing paths."""
        return self.config_store.list_backups()

    def create_backup(self, name: str) -> ManagedBackup:
        """Back up the last successfully saved canonical configuration."""
        with self._coordination_lock:
            saved = self._saved_configuration
        if saved is None:
            raise ManagedBackupError("no_saved_configuration") from None
        return self.config_store.create_backup(name, saved)

    def restore_backup(self, backup_id: str) -> ConfigurationDocumentV2:
        """Validate a backup through schema and compositor and return a draft."""
        configuration = self.config_store.restore_backup(backup_id)
        self.validate_configuration(configuration)
        return configuration

    def delete_backup(self, backup_id: str) -> None:
        """Delete one validated user-managed backup."""
        self.config_store.delete_backup(backup_id)

    @property
    def render_task(self) -> asyncio.Task[None] | None:
        return self._render_task

    @property
    def owns_resources(self) -> bool:
        return self._worker_started or self._render_task is not None

    async def start(self) -> None:
        if self.owns_resources:
            raise RuntimeError("NexTuxLinqService is already started.")
        self.state.update(lifecycle_state="starting", rendering_state="idle")
        try:
            self._event_loop = asyncio.get_running_loop()
            self._worker_failure_event = asyncio.Event()
            self._render_wakeup = asyncio.Event()
            self._worker_exit_observed.clear()
            loaded = self.config_store.load()
            configuration = _canonical_configuration(loaded.configuration)
            with self._coordination_lock:
                self._configuration = configuration
                self._saved_configuration = (
                    configuration if loaded.persisted else None
                )
                self._saved_revision = 1 if loaded.persisted else None
                self._saved_revision_counter = self._saved_revision or 0
                self._migration_pending = loaded.migration_pending
                self.state.update(
                    active_configuration_revision=1,
                    configuration_warning=loaded.warning,
                )
            self.device_worker.set_status_callback(
                self.handle_worker_snapshot
            )
            self.device_worker.start()
            self._worker_started = True
            if configuration.active.brightness is not None:
                self.device_worker.set_brightness(
                    configuration.active.brightness,
                    revision=1,
                )
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
            self.state.update(
                lifecycle_state="failed",
                rendering_state="failed",
                last_display_error="NexTuxLinq service startup failed.",
            )
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
        event = self._worker_failure_event
        if event is None:
            raise RuntimeError("NexTuxLinqService has not been started.")
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

    async def _wait_for_update(self, delay: float | None) -> str:
        wakeup = self._render_wakeup
        if wakeup is None:
            raise RuntimeError("The render scheduler is unavailable.")
        if delay is None:
            await wakeup.wait()
            return "change"
        sleep_task = asyncio.create_task(self._sleep(max(0.0, delay)))
        wake_task = asyncio.create_task(wakeup.wait())
        try:
            done, pending = await asyncio.wait(
                (sleep_task, wake_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if wake_task in done:
                await wake_task
                return "change"
            await sleep_task
            return "periodic"
        finally:
            for task in (sleep_task, wake_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                sleep_task,
                wake_task,
                return_exceptions=True,
            )

    async def _render_loop(self) -> None:
        if self._render_wakeup is None:
            self._render_wakeup = asyncio.Event()
        reason = "initial"
        while True:
            async with self._mutation_lock:
                profile, revision = self._capture_active_profile()
                existing = (
                    reason == "change"
                    and self._last_render_revision == revision
                    and self._last_render_result is not None
                )
                if existing:
                    result = self._last_render_result
                    wall_clock = result.generation_time
                else:
                    result, generated_at = self._render_profile(profile)
                    self.device_worker.submit_frame(
                        result.device_framebuffer,
                        revision=revision,
                    )
                    self._last_render_result = result
                    self._last_render_revision = revision
                    wall_clock = result.generation_time
                    self.state.update(
                        last_frame_generation_time=generated_at,
                        update_policy=result.update_policy.value,
                    )
                wakeup = self._render_wakeup
                if wakeup is None:
                    raise RuntimeError("The render scheduler is unavailable.")
                wakeup.clear()
                delay = update_policy_delay(
                    result.update_policy,
                    wall_clock=wall_clock,
                    monotonic_now=self._monotonic(),
                    continuous_epoch=self._started_at,
                    frame_rate=self.frame_rate,
                    profile=profile,
                )
            reason = await self._wait_for_update(delay)

    def _render_profile(
        self,
        profile: ProfileConfigV2,
    ) -> tuple[RenderResult, float]:
        generated_at = self._monotonic()
        wall_clock = self._wall_clock()
        metrics_snapshot: SystemMetricsSnapshot | None = None
        if any(
            region.enabled
            and region.widget.type in {
                WidgetType.SYSTEM_METRIC,
                WidgetType.METRIC_BAR,
            }
            for region in profile.regions
        ):
            sample_failed = False
            try:
                candidate_snapshot = self._metrics_provider.sample()
                if type(candidate_snapshot) is not SystemMetricsSnapshot:
                    sample_failed = True
            except Exception:
                sample_failed = True
                candidate_snapshot = None
            metrics_snapshot = (
                SystemMetricsSnapshot.unavailable(generated_at, wall_clock)
                if sample_failed
                else candidate_snapshot
            )
            self._last_metrics_snapshot = metrics_snapshot
        failure: tuple[str, str | None, WidgetType | None, str | None] | None
        try:
            result = self._compositor.render(
                profile,
                generation_time=wall_clock,
                animation_time=max(0.0, generated_at - self._started_at),
                metrics_snapshot=metrics_snapshot,
            )
        except CompositorError as error:
            failure = (
                error.category.value,
                error.region_id,
                error.widget_type,
                error.field,
            )
            result = None
        else:
            failure = None
        if failure is not None:
            category, region_id, widget_type, field = failure
            raise CandidateValidationError(
                category,
                region_id=region_id,
                widget_type=widget_type,
                field=field,
            )
        assert result is not None
        return result, generated_at

    def get_configuration(self) -> ServiceConfigurationSnapshot:
        with self._coordination_lock:
            configuration = self.configuration
            try:
                legacy = legacy_configuration_from_v2(configuration)
            except LegacyEditorUnsupportedError:
                legacy = None
            return ServiceConfigurationSnapshot(
                configuration=configuration,
                built_in_default=legacy_equivalent_configuration_v2(),
                active_revision=self.snapshot().active_configuration_revision,
                saved_revision=self._saved_revision,
                dirty=self._saved_configuration != configuration,
                migration_pending=self._migration_pending,
                legacy_configuration=legacy,
                legacy_built_in_default=default_configuration(),
                legacy_editor_supported=legacy is not None,
            )

    async def apply_profile(
        self,
        profile_name: str,
        profile: ProfileConfig,
        *,
        expected_revision: int | None = None,
    ) -> ApplyResult:
        candidate = apply_legacy_profile(
            self.configuration,
            profile_name,
            profile,
        )
        result = await self.apply_configuration(
            candidate,
            expected_revision=expected_revision,
        )
        return ApplyResult(
            profile_name=profile_name,
            profile=profile,
            active_revision=result.active_revision,
            dirty=result.dirty,
        )

    async def apply_configuration(
        self,
        configuration: ConfigurationDocumentV2,
        *,
        expected_revision: int | None = None,
    ) -> CanonicalApplyResult:
        if type(configuration) is not ConfigurationDocumentV2:
            raise TypeError(
                "configuration must be a ConfigurationDocumentV2."
            )
        async with self._mutation_lock:
            with self._coordination_lock:
                self._require_running()
                current_revision = self.snapshot().active_configuration_revision
                self._check_revision(expected_revision, current_revision)
            rendered: dict[str, tuple[RenderResult, float]] = {}
            for name, profile in configuration.profiles.items():
                rendered[name] = self._render_profile(profile)
            result, generated_at = rendered[
                configuration.active_profile
            ]
            new_revision = current_revision + 1
            await self._mutation_gate("apply", new_revision)
            with self._coordination_lock:
                self._require_running()
                self._submit_candidate(
                    result.device_framebuffer,
                    configuration.active.brightness,
                    revision=new_revision,
                )
                self._configuration = configuration
                self._last_render_result = result
                self._last_render_revision = new_revision
                self.state.update(
                    active_configuration_revision=new_revision,
                    last_frame_generation_time=generated_at,
                    update_policy=result.update_policy.value,
                )
                dirty = self._saved_configuration != configuration
                if self._render_wakeup is not None:
                    self._render_wakeup.set()
        return CanonicalApplyResult(
            configuration=configuration,
            render_result=result,
            active_revision=new_revision,
            dirty=dirty,
        )

    async def select_profile(
        self,
        profile_name: str,
        *,
        expected_revision: int | None = None,
    ) -> CanonicalApplyResult:
        current = self.configuration
        if profile_name not in current.profiles:
            raise ValueError("profile_name must identify an existing profile.")
        return await self.apply_configuration(
            ConfigurationDocumentV2(
                schema_version=current.schema_version,
                active_profile=profile_name,
                profiles=current.profiles,
            ),
            expected_revision=expected_revision,
        )

    def _submit_candidate(
        self,
        frame: bytes,
        brightness: int | None,
        *,
        revision: int,
    ) -> None:
        submission_failed = False
        frame_accepted = False
        try:
            self.device_worker.submit_frame(frame, revision=revision)
            frame_accepted = True
            if brightness is not None:
                self.device_worker.set_brightness(
                    brightness,
                    revision=revision,
                )
        except Exception:
            submission_failed = True
        if submission_failed:
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
            )

    async def save_configuration(
        self,
        configuration: ConfigurationDocument,
        *,
        expected_active_revision: int | None = None,
        expected_saved_revision: int | None = None,
        apply: bool = True,
    ) -> SaveResult:
        candidate = apply_legacy_configuration(
            self.configuration,
            configuration,
        )
        result = await self.save_canonical_configuration(
            candidate,
            expected_active_revision=expected_active_revision,
            expected_saved_revision=expected_saved_revision,
            apply=apply,
        )
        return SaveResult(
            configuration=configuration,
            active_revision=result.active_revision,
            saved_revision=result.saved_revision,
            dirty=result.dirty,
            warning=result.warning,
        )

    async def save_canonical_configuration(
        self,
        configuration: ConfigurationDocumentV2,
        *,
        expected_active_revision: int | None = None,
        expected_saved_revision: int | None = None,
        apply: bool = True,
    ) -> CanonicalSaveResult:
        if type(configuration) is not ConfigurationDocumentV2:
            raise TypeError(
                "configuration must be a ConfigurationDocumentV2."
            )
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
            rendered: dict[str, tuple[RenderResult, float]] = {}
            for name, profile in configuration.profiles.items():
                result, generated_at = self._render_profile(profile)
                rendered[name] = (result, generated_at)
            await self._mutation_gate(
                "save-apply" if apply else "save",
                current_revision + int(apply),
            )
            warning: str | None = None
            save_failed = False
            committed = False
            try:
                self.config_store.save(configuration)
            except ConfigSaveError as error:
                committed = error.committed
                save_failed = not committed
            if save_failed:
                raise ConfigSaveError(
                    "Configuration save failed before the commit point.",
                    committed=False,
                )
            if committed:
                warning = (
                    "Configuration was saved, but directory durability "
                    "could not be confirmed."
                )
            with self._coordination_lock:
                self._saved_configuration = configuration
                self._saved_revision_counter += 1
                self._saved_revision = self._saved_revision_counter
                self._migration_pending = False
                self.state.update(configuration_warning=warning)
                if apply:
                    self._require_running()
                    new_revision = current_revision + 1
                    result, generated_at = rendered[
                        configuration.active_profile
                    ]
                    self._submit_candidate(
                        result.device_framebuffer,
                        configuration.active.brightness,
                        revision=new_revision,
                    )
                    self._configuration = configuration
                    self._last_render_result = result
                    self._last_render_revision = new_revision
                    self.state.update(
                        active_configuration_revision=new_revision,
                        last_frame_generation_time=generated_at,
                        update_policy=result.update_policy.value,
                    )
                    if self._render_wakeup is not None:
                        self._render_wakeup.set()
                else:
                    new_revision = current_revision
                saved_revision = self._saved_revision
                dirty = self._saved_configuration != self.configuration
        return CanonicalSaveResult(
            configuration=configuration,
            active_revision=new_revision,
            saved_revision=saved_revision,
            dirty=dirty,
            migration_pending=False,
            warning=warning,
        )

    def validate_configuration(
        self,
        configuration: ConfigurationDocumentV2,
    ) -> RenderResult:
        if type(configuration) is not ConfigurationDocumentV2:
            raise TypeError(
                "configuration must be a ConfigurationDocumentV2."
            )
        rendered = {
            name: self._render_profile(profile)[0]
            for name, profile in configuration.profiles.items()
        }
        result = rendered[configuration.active_profile]
        return result

    def get_preview(self) -> PreviewSnapshot:
        generated_at = self._monotonic()
        profile, revision = self._capture_active_profile()
        result, _ = self._render_profile(profile)
        return PreviewSnapshot(
            framebuffer=result.logical_framebuffer,
            device_framebuffer=result.device_framebuffer,
            active_revision=revision,
            generated_at=generated_at,
            profile=profile,
            render_result=result,
        )

    def _capture_active_profile(self) -> tuple[ProfileConfigV2, int]:
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
                "The NexTuxLinq service is not accepting configuration changes."
            )

    def handle_worker_snapshot(self, snapshot: DeviceWorkerSnapshot) -> None:
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
