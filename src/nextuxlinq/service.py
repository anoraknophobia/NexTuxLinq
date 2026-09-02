"""Framework-independent canonical schema-v2 service orchestration."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from nextuxlinq.config_store import (
    ConfigSaveError,
    ConfigStore,
    ManagedBackup,
    ManagedBackupError,
)
from nextuxlinq.creator import MATRIX_HEIGHT, MATRIX_WIDTH
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
    CanvasConfig,
    ConfigurationDocumentV2,
    DisplayEffect,
    ProfileConfigV2,
    RegionConfig,
)
from nextuxlinq.rendering_v2.compositor import (
    CONTENT_DEPENDENT_FAILURES,
    Compositor,
    CompositorError,
    RenderResult,
)
from nextuxlinq.rendering_v2.fonts import (
    AUTO_FONT_PRIORITY,
    FontDefinition,
    FontFitAnalysis,
    create_builtin_font_registry,
)
from nextuxlinq.rendering_v2.registry import (
    RenderContext,
    create_widget_registry,
)
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.update_policy import UpdatePolicy
from nextuxlinq.rendering_v2.widgets.font_fit import analyze_widget_font_fit
from nextuxlinq.scheduler import update_policy_delay
from nextuxlinq.state import DaemonState, DaemonSnapshot
from nextuxlinq.system_metrics import (
    LinuxSystemMetricsProvider,
    MetricAvailability,
    MetricId,
    MetricReason,
    MetricsSnapshotCoordinator,
    SystemMetricsSnapshot,
)
from nextuxlinq.widget_models import (
    ClockSettings,
    DateSettings,
    FontChoice,
    MetricBarSettings,
    MetricDisplayMode,
    ScrollingTextSettings,
    StaticTextSettings,
    SystemMetricSettings,
    WidgetType,
)

AsyncSleep = Callable[[float], Awaitable[None]]
WallClock = Callable[[], datetime]
TaskFactory = Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]]
MutationGate = Callable[[str, int], Awaitable[None]]

LOGGER = logging.getLogger(__name__)


def font_fit_payload(analysis: FontFitAnalysis) -> dict[str, object]:
    """Serialize immutable font diagnostics without content or implementation data."""
    def font_value(value: FontChoice | None) -> str | None:
        return None if value is None else value.value

    return {
        "actual_width": analysis.actual_width,
        "actual_height": analysis.actual_height,
        "selected_explicit_font": font_value(analysis.selected_explicit_font),
        "resolved_auto_font": font_value(analysis.resolved_auto_font),
        "largest_fitting_font": font_value(analysis.largest_fitting_font),
        "smallest_fitting_font": font_value(analysis.smallest_fitting_font),
        "best_smaller_alternative": font_value(
            analysis.best_smaller_alternative
        ),
        "no_font_fits": analysis.no_font_fits,
        "no_font_supports_content": analysis.no_font_supports_content,
        "candidates": tuple(
            {
                "font_id": item.font_id.value,
                "display_label": item.display_label,
                "glyph_width": item.glyph_width,
                "glyph_height": item.glyph_height,
                "compatible_with_widget": item.compatible_with_widget,
                "complete_glyph_coverage": item.complete_glyph_coverage,
                "fits_height": item.fits_height,
                "fits_width": item.fits_width,
                "overall_fit": item.overall_fit,
                "minimum_width": item.minimum_width,
                "minimum_height": item.minimum_height,
                "rejection_category": item.rejection_category,
            }
            for item in analysis.candidates
        ),
    }


class RevisionConflictError(RuntimeError):
    """Raised when a mutation targets a stale runtime revision."""


class ServiceUnavailableError(RuntimeError):
    """Raised when runtime mutations are no longer accepted."""


class WorkerSubmissionError(RuntimeError):
    """A sanitized candidate submission failure."""


@dataclass(frozen=True, slots=True)
class CandidateValidationIssue:
    """Safe structured details for one invalid candidate region."""

    category: str
    region_id: str | None = None
    widget_type: WidgetType | None = None
    field: str | None = None
    actual_width: int | None = None
    actual_height: int | None = None
    minimum_width: int | None = None
    minimum_height: int | None = None
    font_fit: FontFitAnalysis | None = None


@dataclass(frozen=True, slots=True)
class WidgetContentPreview:
    framebuffer: bytes
    preview_width: int
    preview_height: int
    current_width: int
    current_height: int
    fit_state: str
    resolved_font: str | None
    minimum_width: int | None
    minimum_height: int | None
    current_output: str | None
    output_is_example: bool
    suggested_font: str | None
    suggest_value_only: bool


class CandidateValidationError(ValueError):
    """A sanitized complete-candidate rendering failure."""

    def __init__(
        self,
        category: str,
        *,
        region_id: str | None = None,
        widget_type: WidgetType | None = None,
        field: str | None = None,
        actual_width: int | None = None,
        actual_height: int | None = None,
        minimum_width: int | None = None,
        minimum_height: int | None = None,
        font_fit: FontFitAnalysis | None = None,
        issues: tuple[CandidateValidationIssue, ...] | None = None,
    ) -> None:
        self.category = category
        self.region_id = region_id
        self.widget_type = widget_type
        self.field = field
        self.actual_width = actual_width
        self.actual_height = actual_height
        self.minimum_width = minimum_width
        self.minimum_height = minimum_height
        self.font_fit = font_fit
        self.issues = issues or (CandidateValidationIssue(
            category=category,
            region_id=region_id,
            widget_type=widget_type,
            field=field,
            actual_width=actual_width,
            actual_height=actual_height,
            minimum_width=minimum_width,
            minimum_height=minimum_height,
            font_fit=font_fit,
        ),)
        context = ""
        if region_id is not None:
            context = f" Region {region_id!r}"
            if widget_type is not None:
                context += f", widget {widget_type.value!r}"
            context += "."
        if field is not None:
            context += f" Field {field!r}."
        super().__init__(
            "The free-layout candidate could not be rendered. "
            f"Category {category!r}.{context}"
        )


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


#: Sentinel distinguishing "resolve a snapshot for this profile" from an
#: explicit ``None`` meaning "this render needs no metrics at all".
_RESOLVE_METRICS: Any = object()


def profile_requires_metrics(profile: ProfileConfigV2) -> bool:
    """Return whether any enabled region of a profile reads system metrics."""
    return any(
        region.enabled
        and region.widget.type in {
            WidgetType.SYSTEM_METRIC,
            WidgetType.METRIC_BAR,
        }
        for region in profile.regions
    )


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
        # The coordinator is the only component allowed to advance the
        # stateful CPU and network deltas, and it does so at most once per
        # minimum sampling interval no matter how often previews are polled.
        self._metrics = MetricsSnapshotCoordinator(
            LinuxSystemMetricsProvider(
                monotonic=monotonic,
                wall_clock=wall_clock,
            )
            if metrics_provider is None
            else metrics_provider,
            monotonic=monotonic,
            wall_clock=wall_clock,
        )
        self._widget_registry = create_widget_registry()
        self._compositor = Compositor(self._widget_registry)
        self._configuration: ConfigurationDocumentV2 | None = None
        self._render_task: asyncio.Task[None] | None = None
        self._render_wakeup: asyncio.Event | None = None
        self._initial_render_ready: asyncio.Event | None = None
        self._started_at = 0.0
        self._last_worker_uploads = 0
        self._worker_started = False
        self._saved_configuration: ConfigurationDocumentV2 | None = None
        self._saved_revision: int | None = None
        self._saved_revision_counter = 0
        self._migration_pending = False
        self._last_render_result: RenderResult | None = None
        self._last_render_revision: int | None = None
        self._retained_frame_category: str | None = None
        self._reported_retentions: set[str] = set()
        self._mutation_lock = asyncio.Lock()
        self._coordination_lock = threading.RLock()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._worker_failure_event: asyncio.Event | None = None
        self._worker_exit_observed = threading.Event()

    @property
    def _metrics_provider(self) -> Any:
        """Return the stateful provider owned by the snapshot coordinator."""
        return self._metrics.provider

    @_metrics_provider.setter
    def _metrics_provider(self, provider: Any) -> None:
        self._metrics.replace_provider(provider)

    @property
    def _last_metrics_snapshot(self) -> SystemMetricsSnapshot | None:
        """Return the shared snapshot without advancing provider deltas."""
        return self._metrics.current_snapshot()

    def _shared_metrics_snapshot(
        self,
        profiles: Iterable[ProfileConfigV2],
    ) -> SystemMetricsSnapshot | None:
        """Resolve one snapshot shared by every profile of a single operation.

        Rendering several profiles must never sample once per profile: each
        sample advances the CPU and network baselines, so per-profile sampling
        would compress the measured interval to a few milliseconds.
        """
        if not any(profile_requires_metrics(profile) for profile in profiles):
            return None
        return self._metrics.sample_if_due()

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

    def get_font_definitions(self) -> tuple[FontDefinition, ...]:
        """Return concrete fonts in the explicit canonical Auto ranking."""
        registry = create_builtin_font_registry()
        return tuple(registry.resolve(font_id) for font_id in AUTO_FONT_PRIORITY)

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
            self._initial_render_ready = asyncio.Event()
            self._worker_exit_observed.clear()
            self._retained_frame_category = None
            self._reported_retentions = set()
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
                if self._worker_exit_observed.is_set():
                    raise RuntimeError(
                        "The Matrix900 device worker stopped during startup."
                    )
            await self._await_initial_render()
            with self._coordination_lock:
                worker_snapshot = self.device_worker.snapshot()
                if (
                    not worker_snapshot.running
                    or self.snapshot().lifecycle_state != "starting"
                    or self._worker_exit_observed.is_set()
                ):
                    raise RuntimeError(
                        "The Matrix900 device worker stopped during startup."
                    )
                self.state.update(
                    lifecycle_state="running",
                    rendering_state="rendering",
                )
                if (
                    self._worker_exit_observed.is_set()
                    or not self.device_worker.snapshot().running
                ):
                    raise RuntimeError(
                        "The Matrix900 device worker stopped during startup."
                    )
        except BaseException:
            failure = self.snapshot()
            display_error = (
                failure.last_display_error
                if failure.rendering_state == "failed"
                and failure.last_display_error == "Matrix900 rendering failed."
                else "NexTuxLinq service startup failed."
            )
            try:
                await self._cleanup_resources()
            except BaseException:
                pass
            self.state.update(
                lifecycle_state="failed",
                rendering_state="failed",
                last_display_error=display_error,
            )
            raise

    async def _await_initial_render(self) -> None:
        task = self._render_task
        ready = self._initial_render_ready
        if task is None or ready is None:
            raise RuntimeError("The render scheduler is unavailable.")
        ready_task = asyncio.create_task(ready.wait())
        try:
            done, _ = await asyncio.wait(
                (task, ready_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                await task
                raise RuntimeError(
                    "The render scheduler stopped during startup."
                )
        finally:
            if not ready_task.done():
                ready_task.cancel()
            await asyncio.gather(ready_task, return_exceptions=True)

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
        self._initial_render_ready = None

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
                    skipped = False
                    try:
                        result, generated_at = self._render_profile(profile)
                    except CandidateValidationError as error:
                        if not self._retain_frame_after(error):
                            raise
                        skipped = True
                    if skipped:
                        # The already accepted profile stays active and the
                        # device keeps the last transmitted frame, so nothing
                        # is submitted for this cadence step.
                        result = self._last_render_result
                        assert result is not None
                        wall_clock = self._wall_clock()
                    else:
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
                        self._resume_after_retained_frames()
                        if (
                            reason == "initial"
                            and self._initial_render_ready is not None
                        ):
                            self._initial_render_ready.set()
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

    def _retain_frame_after(self, error: CandidateValidationError) -> bool:
        """Report whether one failed frame may keep the last transmitted frame.

        Only a content-dependent failure of a profile that already rendered
        successfully is recoverable: the next sample or wall-clock value can
        compose normally again. A failure before the first successful frame,
        or any category that means the profile or the renderer is broken, is
        never retained and still stops the renderer.
        """
        if (
            self._last_render_result is None
            or error.category not in CONTENT_DEPENDENT_FAILURES
        ):
            return False
        # A value at the edge of its range can alternate every cadence step,
        # so each category is reported to the journal once per run. The
        # published state stays live and is the authoritative diagnostic.
        if error.category not in self._reported_retentions:
            self._reported_retentions.add(error.category)
            LOGGER.warning(
                "Matrix900 kept the last frame; the active profile could not "
                "be rendered (%s). Rendering resumes on the next value that "
                "composes.",
                error.category,
            )
        self._retained_frame_category = error.category
        self.state.update(
            rendering_state="degraded",
            last_display_error=(
                f"Matrix900 kept the last frame: {error.category}."
            ),
        )
        return True

    def _resume_after_retained_frames(self) -> None:
        """Publish recovery once a later frame composes successfully again."""
        if self._retained_frame_category is None:
            return
        self._retained_frame_category = None
        self.state.update(
            rendering_state="rendering",
            last_display_error=None,
        )

    def _render_profile(
        self,
        profile: ProfileConfigV2,
        *,
        metrics_snapshot: SystemMetricsSnapshot | None = _RESOLVE_METRICS,
    ) -> tuple[RenderResult, float]:
        generated_at = self._monotonic()
        wall_clock = self._wall_clock()
        if metrics_snapshot is _RESOLVE_METRICS:
            metrics_snapshot = self._shared_metrics_snapshot((profile,))
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
            issues = self._candidate_validation_issues(
                profile,
                wall_clock=wall_clock,
                animation_time=max(0.0, generated_at - self._started_at),
                metrics_snapshot=metrics_snapshot,
            )
            primary = next(
                (issue for issue in issues if issue.region_id == region_id),
                CandidateValidationIssue(
                    category=category,
                    region_id=region_id,
                    widget_type=widget_type,
                    field=field,
                ),
            )
            raise CandidateValidationError(
                primary.category,
                region_id=primary.region_id,
                widget_type=primary.widget_type,
                field=primary.field,
                actual_width=primary.actual_width,
                actual_height=primary.actual_height,
                minimum_width=primary.minimum_width,
                minimum_height=primary.minimum_height,
                font_fit=primary.font_fit,
                issues=issues or (primary,),
            )
        assert result is not None
        return result, generated_at

    def _candidate_validation_issues(
        self,
        profile: ProfileConfigV2,
        *,
        wall_clock: datetime,
        animation_time: float,
        metrics_snapshot: SystemMetricsSnapshot | None,
    ) -> tuple[CandidateValidationIssue, ...]:
        issues: list[CandidateValidationIssue] = []
        for region in profile.regions:
            if not region.enabled:
                continue
            failure = self._isolated_region_failure(
                profile,
                region,
                wall_clock=wall_clock,
                animation_time=animation_time,
                metrics_snapshot=metrics_snapshot,
            )
            if failure is None:
                continue
            category, region_id, widget_type, field = failure
            font_fit = analyze_widget_font_fit(
                region.widget.settings,
                region_width=region.width,
                region_height=region.height,
            )
            minimum_width: int | None = None
            minimum_height: int | None = None
            if category in {"content_too_wide", "content_too_tall"}:
                guidance = None
                if font_fit is not None:
                    guidance = font_fit.selected_candidate
                    if guidance is None:
                        guidance = next(
                            (
                                item for item in reversed(font_fit.candidates)
                                if item.compatible_with_widget
                                and item.complete_glyph_coverage
                            ),
                            None,
                        )
                if guidance is not None:
                    minimum_width = guidance.minimum_width
                    minimum_height = guidance.minimum_height
                else:
                    minimum_width, minimum_height = self._minimum_render_size(
                        region,
                        wall_clock=wall_clock,
                        animation_time=animation_time,
                        metrics_snapshot=metrics_snapshot,
                    )
            issues.append(CandidateValidationIssue(
                category=category,
                region_id=region_id,
                widget_type=widget_type,
                field=field,
                actual_width=region.width,
                actual_height=region.height,
                minimum_width=minimum_width,
                minimum_height=minimum_height,
                font_fit=font_fit,
            ))
        return tuple(issues)

    def _isolated_region_failure(
        self,
        profile: ProfileConfigV2,
        region: RegionConfig,
        *,
        wall_clock: datetime,
        animation_time: float,
        metrics_snapshot: SystemMetricsSnapshot | None,
    ) -> tuple[str, str | None, WidgetType | None, str | None] | None:
        isolated = ProfileConfigV2.from_dict({
            "brightness": profile.brightness,
            "canvas": profile.canvas.to_dict(),
            "regions": [region.to_dict()],
        })
        try:
            self._compositor.render(
                isolated,
                generation_time=wall_clock,
                animation_time=animation_time,
                metrics_snapshot=metrics_snapshot,
            )
        except CompositorError as error:
            return (
                error.category.value,
                error.region_id,
                error.widget_type,
                error.field,
            )
        return None

    def _minimum_render_size(
        self,
        region: RegionConfig,
        *,
        wall_clock: datetime,
        animation_time: float,
        metrics_snapshot: SystemMetricsSnapshot | None,
    ) -> tuple[int | None, int | None]:
        """Probe the real renderer for safe, canvas-bounded size guidance."""
        definition = self._widget_registry.resolve(region.widget.type)
        renderer = definition.renderer
        if renderer is None:
            return None, None

        def renders(width: int, height: int) -> bool:
            try:
                surface = RegionSurface(width, height, RgbColor(0, 0, 0))
                context = RenderContext(
                    wall_clock=wall_clock,
                    animation_time=animation_time,
                    canvas_width=MATRIX_WIDTH,
                    canvas_height=MATRIX_HEIGHT,
                    region_id=region.id,
                    region_width=width,
                    region_height=height,
                    metrics_snapshot=metrics_snapshot,
                )
                return renderer(
                    surface,
                    region.widget.settings,
                    context,
                ) is None
            except Exception:
                return False

        probe_height = region.height
        minimum_width = next(
            (
                width for width in range(1, MATRIX_WIDTH + 1)
                if renders(width, probe_height)
            ),
            None,
        )
        if minimum_width is None and probe_height != MATRIX_HEIGHT:
            probe_height = MATRIX_HEIGHT
            minimum_width = next(
                (
                    width for width in range(1, MATRIX_WIDTH + 1)
                    if renders(width, probe_height)
                ),
                None,
            )

        probe_width = max(region.width, minimum_width or region.width)
        minimum_height = next(
            (
                height for height in range(1, MATRIX_HEIGHT + 1)
                if renders(probe_width, height)
            ),
            None,
        )
        if minimum_height is None and probe_width != MATRIX_WIDTH:
            minimum_height = next(
                (
                    height for height in range(1, MATRIX_HEIGHT + 1)
                    if renders(MATRIX_WIDTH, height)
                ),
                None,
            )
        return minimum_width, minimum_height

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
            shared_metrics = self._shared_metrics_snapshot(
                configuration.profiles.values()
            )
            for name, profile in configuration.profiles.items():
                rendered[name] = self._render_profile(
                    profile, metrics_snapshot=shared_metrics
                )
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
            shared_metrics = self._shared_metrics_snapshot(
                configuration.profiles.values()
            )
            for name, profile in configuration.profiles.items():
                result, generated_at = self._render_profile(
                    profile, metrics_snapshot=shared_metrics
                )
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
        shared_metrics = self._shared_metrics_snapshot(
            configuration.profiles.values()
        )
        rendered = {
            name: self._render_profile(
                profile, metrics_snapshot=shared_metrics
            )[0]
            for name, profile in configuration.profiles.items()
        }
        result = rendered[configuration.active_profile]
        return result

    def get_font_validation(
        self,
        profile: ProfileConfigV2,
    ) -> tuple[
        dict[str, str],
        dict[str, list[dict[str, object]]],
        dict[str, dict[str, object]],
    ]:
        """Return authoritative resolved fonts and explicit-option status."""
        if type(profile) is not ProfileConfigV2:
            raise TypeError("profile must be a ProfileConfigV2.")
        resolved: dict[str, str] = {}
        options: dict[str, list[dict[str, object]]] = {}
        analyses: dict[str, dict[str, object]] = {}
        for region in profile.regions:
            if not region.enabled:
                continue
            analysis = analyze_widget_font_fit(
                region.widget.settings,
                region_width=region.width,
                region_height=region.height,
            )
            if analysis is None:
                continue
            analyses[region.id] = font_fit_payload(analysis)
            concrete = analysis.resolved_auto_font
            if concrete is None and analysis.selected_candidate is not None:
                if analysis.selected_candidate.overall_fit:
                    concrete = analysis.selected_explicit_font
            if concrete is not None:
                resolved[region.id] = concrete.value
            region_options = []
            category_map = {
                "too_wide": "content_too_wide",
                "too_tall": "content_too_tall",
                "unsupported_characters": "unsupported_glyph",
                "unsupported_by_widget": "unsupported_by_widget",
            }
            for item in analysis.candidates:
                region_options.append({
                    "id": item.font_id.value,
                    "available": item.overall_fit,
                    "category": category_map.get(item.rejection_category),
                    "minimum_width": item.minimum_width,
                    "minimum_height": item.minimum_height,
                })
            options[region.id] = region_options
        return resolved, options, analyses

    @staticmethod
    def _preview_example_value(metric_id: MetricId) -> float:
        return {
            MetricId.CPU_USAGE: 14.0,
            MetricId.MEMORY_USAGE: 14.0,
            MetricId.CPU_TEMPERATURE: 43.0,
            MetricId.UPTIME: float(3 * 3600 + 41 * 60),
            MetricId.LOAD_AVERAGE_1M: 1.24,
            MetricId.STORAGE_USAGE: 14.0,
            MetricId.NETWORK_RECEIVE_RATE: 1.2 * 1024.0 ** 2,
            MetricId.NETWORK_TRANSMIT_RATE: 640.0 * 1024.0,
            MetricId.GPU_USAGE: 14.0,
            MetricId.GPU_TEMPERATURE: 58.0,
            MetricId.GPU_MEMORY_USAGE: 14.0,
            MetricId.STORAGE_TEMPERATURE: 41.0,
        }[metric_id]

    def _preview_metrics(
        self,
        metric_id: MetricId,
        wall_clock: datetime,
    ) -> tuple[SystemMetricsSnapshot, bool]:
        snapshot = self._last_metrics_snapshot
        if snapshot is not None and snapshot.value(metric_id) is not None:
            return snapshot, False
        availability = {
            item: (
                MetricAvailability.AVAILABLE
                if item is metric_id else MetricAvailability.UNAVAILABLE
            )
            for item in MetricId
        }
        reasons = {
            item: (
                None if item is metric_id else MetricReason.SOURCE_NOT_PRESENT
            )
            for item in MetricId
        }
        return SystemMetricsSnapshot(
            sample_monotonic=0.0,
            sample_time=wall_clock,
            values={metric_id: self._preview_example_value(metric_id)},
            availability=availability,
            source_status="partial",
            reasons=reasons,
        ), True

    def _widget_renders_at(
        self,
        region: RegionConfig,
        width: int,
        height: int,
        *,
        wall_clock: datetime,
        metrics_snapshot: SystemMetricsSnapshot | None,
    ) -> bool:
        definition = self._widget_registry.resolve(region.widget.type)
        if definition.renderer is None:
            return False
        try:
            surface = RegionSurface(width, height, RgbColor(0, 0, 0))
            context = RenderContext(
                wall_clock=wall_clock,
                animation_time=0.0,
                canvas_width=MATRIX_WIDTH,
                canvas_height=MATRIX_HEIGHT,
                region_id=region.id,
                region_width=width,
                region_height=height,
                metrics_snapshot=metrics_snapshot,
            )
            definition.renderer(surface, region.widget.settings, context)
        except Exception:
            return False
        return True

    def _widget_intrinsic_size(
        self,
        region: RegionConfig,
        *,
        wall_clock: datetime,
        metrics_snapshot: SystemMetricsSnapshot | None,
    ) -> tuple[int | None, int | None]:
        """Resolve minimum geometry from canonical analysis, then bounded fallback."""
        analysis = analyze_widget_font_fit(
            region.widget.settings,
            region_width=MATRIX_WIDTH,
            region_height=MATRIX_HEIGHT,
        )
        if analysis is not None:
            guidance = analysis.selected_candidate
            if guidance is None:
                guidance = next(
                    (
                        item for item in reversed(analysis.candidates)
                        if item.compatible_with_widget
                        and item.complete_glyph_coverage
                    ),
                    None,
                )
            if guidance is not None:
                return guidance.minimum_width, guidance.minimum_height
            return None, None
        definition = self._widget_registry.resolve(region.widget.type)
        if definition.minimum_width is not None and definition.minimum_height is not None:
            return definition.minimum_width, definition.minimum_height
        for height in range(1, MATRIX_HEIGHT + 1):
            for width in range(1, MATRIX_WIDTH + 1):
                if self._widget_renders_at(
                    region,
                    width,
                    height,
                    wall_clock=wall_clock,
                    metrics_snapshot=metrics_snapshot,
                ):
                    return width, height
        return None, None

    def get_widget_content_preview(
        self,
        region: RegionConfig,
        canvas: CanvasConfig,
        effect: DisplayEffect,
    ) -> WidgetContentPreview:
        """Render one side-effect-free widget preview through the compositor."""
        if type(region) is not RegionConfig or type(canvas) is not CanvasConfig:
            raise TypeError("preview requires canonical region and canvas values.")
        wall_clock = self._wall_clock()
        metrics_snapshot: SystemMetricsSnapshot | None = None
        output_is_example = False
        settings = region.widget.settings
        if isinstance(settings, (SystemMetricSettings, MetricBarSettings)):
            metrics_snapshot, output_is_example = self._preview_metrics(
                settings.metric, wall_clock
            )

        current_fits = self._widget_renders_at(
            region,
            region.width,
            region.height,
            wall_clock=wall_clock,
            metrics_snapshot=metrics_snapshot,
        )
        minimum_width, minimum_height = self._widget_intrinsic_size(
            region,
            wall_clock=wall_clock,
            metrics_snapshot=metrics_snapshot,
        )
        current_analysis = analyze_widget_font_fit(
            settings,
            region_width=region.width,
            region_height=region.height,
        )
        if current_analysis is not None and current_analysis.no_font_supports_content:
            fit_state = "no_available_font_supports_content"
        elif current_fits:
            fit_state = "fits_current_region"
        elif (
            current_analysis is not None
            and current_analysis.best_smaller_alternative is not None
        ):
            fit_state = "would_fit_with_smaller_font"
        else:
            fit_state = "current_region_too_small"

        preview_width = region.width if current_fits else minimum_width
        preview_height = region.height if current_fits else minimum_height
        preview_geometry_is_renderable = (
            preview_width is not None
            and preview_height is not None
            and 1 <= preview_width <= MATRIX_WIDTH
            and 1 <= preview_height <= MATRIX_HEIGHT
        )
        if not preview_geometry_is_renderable:
            preview_width, preview_height = region.width, region.height
            color = RgbColor.from_schema_color(
                region.background_color or canvas.background_color
            ).to_bytes()
            framebuffer = color * (preview_width * preview_height)
        else:
            assert preview_width is not None and preview_height is not None
            preview_region = replace(
                region, x=0, y=0, width=preview_width, height=preview_height
            )
            profile = ProfileConfigV2(
                brightness=None,
                canvas=canvas,
                regions=(preview_region,),
                effect=effect,
            )
            rendered = self._compositor.render(
                profile,
                generation_time=wall_clock,
                animation_time=0.0,
                metrics_snapshot=metrics_snapshot,
            )
            rows = []
            for y in range(preview_height):
                start = y * MATRIX_WIDTH * 3
                rows.append(rendered.logical_framebuffer[
                    start : start + preview_width * 3
                ])
            framebuffer = b"".join(rows)

        preview_analysis = analyze_widget_font_fit(
            settings,
            region_width=preview_width,
            region_height=preview_height,
        )
        resolved_font: str | None = None
        if preview_analysis is not None:
            resolved = preview_analysis.resolved_auto_font
            if resolved is None and preview_analysis.selected_candidate is not None:
                if preview_analysis.selected_candidate.overall_fit:
                    resolved = preview_analysis.selected_explicit_font
            resolved_font = None if resolved is None else resolved.value

        current_output: str | None
        if isinstance(settings, SystemMetricSettings):
            from nextuxlinq.rendering_v2.widgets.system_metric import metric_text

            current_output = metric_text(settings, RenderContext(
                wall_clock=wall_clock,
                animation_time=0.0,
                canvas_width=MATRIX_WIDTH,
                canvas_height=MATRIX_HEIGHT,
                region_id=region.id,
                region_width=preview_width,
                region_height=preview_height,
                metrics_snapshot=metrics_snapshot,
            ))
        elif isinstance(settings, ClockSettings):
            from nextuxlinq.rendering_v2.widgets.clock import format_clock
            from nextuxlinq.rendering_v2.widgets.timezone import resolve_wall_clock

            current_output = format_clock(
                resolve_wall_clock(wall_clock, settings.time_zone),
                settings.format,
                show_seconds=settings.show_seconds,
            )
        elif isinstance(settings, DateSettings):
            from nextuxlinq.rendering_v2.widgets.date import format_date
            from nextuxlinq.rendering_v2.widgets.timezone import resolve_wall_clock

            current_output = format_date(
                resolve_wall_clock(wall_clock, settings.time_zone), settings.format
            )
        elif isinstance(settings, (StaticTextSettings, ScrollingTextSettings)):
            current_output = settings.content.upper() if settings.uppercase else settings.content
        elif isinstance(settings, MetricBarSettings):
            assert metrics_snapshot is not None
            value = metrics_snapshot.value(settings.metric)
            current_output = None if value is None else f"{value:g}"
        elif region.widget.type is WidgetType.BUILT_IN_ICON:
            current_output = "CachyOS Logo"
        else:
            current_output = "Blank"

        suggest_value_only = False
        if (
            isinstance(settings, SystemMetricSettings)
            and settings.display_mode is MetricDisplayMode.LABEL_AND_VALUE
        ):
            value_only_region = replace(
                region,
                widget=replace(
                    region.widget,
                    settings=replace(
                        settings, display_mode=MetricDisplayMode.VALUE_ONLY
                    ),
                ),
            )
            value_width, _ = self._widget_intrinsic_size(
                value_only_region,
                wall_clock=wall_clock,
                metrics_snapshot=metrics_snapshot,
            )
            suggest_value_only = (
                value_width is not None
                and minimum_width is not None
                and value_width < minimum_width
            )

        suggested = (
            None
            if current_analysis is None
            or current_analysis.best_smaller_alternative is None
            else current_analysis.best_smaller_alternative.value
        )
        return WidgetContentPreview(
            framebuffer=framebuffer,
            preview_width=preview_width,
            preview_height=preview_height,
            current_width=region.width,
            current_height=region.height,
            fit_state=fit_state,
            resolved_font=resolved_font,
            minimum_width=minimum_width,
            minimum_height=minimum_height,
            current_output=current_output,
            output_is_example=output_is_example,
            suggested_font=suggested,
            suggest_value_only=suggest_value_only,
        )

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
