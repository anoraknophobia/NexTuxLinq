"""Loopback-only FastAPI application for the Matrix900 service."""

from __future__ import annotations

import base64
import ipaddress
import logging
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exceptions import ResponseValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from nextuxlinq.api_models import (
    ApplyRequest,
    ApplyResponse,
    CanonicalApplyRequest,
    CanonicalApplyResponse,
    CanonicalConfigurationModel,
    CanonicalConfigResponse,
    CanonicalSaveRequest,
    CanonicalSaveResponse,
    CanonicalValidateRequest,
    CanonicalValidateResponse,
    ConfigResponse,
    ConfigurationModel,
    DomainValidationError,
    ErrorResponse,
    FontEditorDefinition,
    FreeLayoutMetadataResponse,
    PreviewRegion,
    PreviewResponse,
    ProfileSelectionRequest,
    ProfileModel,
    SaveRequest,
    SaveResponse,
    StatusResponse,
    WidgetEditorDefinition,
    MetricEditorDefinition,
    MetricDiagnostic,
    MetricDiagnosticsResponse,
    BackupCreateRequest,
    BackupListResponse,
    BackupMetadataResponse,
    BackupRestoreResponse,
    ConfigurationImportResponse,
    ConfigurationImportSummary,
    DeleteResponse,
    ImportFileRequest,
    PresetListResponse,
    PresetResponse,
    ProfileImportRequest,
    ProfileImportResponse,
    ProfileExportRequest,
)
from nextuxlinq.config_store import (
    ConfigSaveError,
    MANAGED_BACKUP_RETENTION,
    ManagedBackup,
    ManagedBackupError,
)
from nextuxlinq.creator import MATRIX_HEIGHT, MATRIX_WIDTH
from nextuxlinq.models import MAX_PROFILES
from nextuxlinq.models_v2 import (
    MAX_REGIONS_PER_DOCUMENT,
    MAX_REGIONS_PER_PROFILE,
    ConfigurationDocumentV2,
)
from nextuxlinq.profile_presets import get_builtin_presets
from nextuxlinq.profile_transfer import (
    TransferValidationError,
    decode_import_content,
    deterministic_json,
    export_profile,
    import_configuration,
    import_profile,
    MAX_IMPORT_BYTES,
    parse_json_bytes,
    safe_profile_filename,
)
from nextuxlinq.service import (
    CandidateValidationError,
    NexTuxLinqService,
    RevisionConflictError,
    ServiceUnavailableError,
)
from nextuxlinq.service_manager import UserServiceStatus
from nextuxlinq.system_metrics import BAR_METRICS, METRIC_DEFINITIONS, MetricId
from nextuxlinq.legacy_compat import LegacyEditorUnsupportedError

LOGGER = logging.getLogger(__name__)
API_PREFIX = "/api/v1"
MAX_REQUEST_BODY = 1024 * 1024
PACKAGE_DIRECTORY = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=PACKAGE_DIRECTORY / "templates")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _error(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    field_errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    payload = ErrorResponse(
        code=code,
        message=message,
        field_errors=field_errors,
        request_id=request_id,
    )
    return JSONResponse(payload.model_dump(), status_code=status_code)


_BRACKETED_AUTHORITY = re.compile(r"^\[([^\]]+)\](?::([0-9]+))?$")


def _parse_authority(authority: str, expected_port: int) -> str:
    """Return a canonical host from one strict HTTP authority."""
    if (
        not authority
        or any(character.isspace() for character in authority)
        or "@" in authority
    ):
        raise ValueError("Invalid Host authority.")
    if authority.startswith("["):
        match = _BRACKETED_AUTHORITY.fullmatch(authority)
        if match is None:
            raise ValueError("Invalid bracketed Host authority.")
        host, port_text = match.groups()
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise ValueError("Invalid IPv6 Host address.") from error
        if address.version != 6:
            raise ValueError("Bracketed Host must be IPv6.")
        canonical = address.compressed
    else:
        if "[" in authority or "]" in authority or authority.count(":") > 1:
            raise ValueError("Invalid Host authority.")
        if ":" in authority:
            host, port_text = authority.split(":", 1)
            if not port_text:
                raise ValueError("Host port must not be empty.")
        else:
            host, port_text = authority, None
        if not host:
            raise ValueError("Host name must not be empty.")
        canonical = host.lower()
    if port_text is not None:
        if not port_text.isascii() or not port_text.isdigit():
            raise ValueError("Host port must be numeric.")
        port = int(port_text)
        if port != expected_port:
            raise ValueError("Host port is not the configured daemon port.")
    return canonical


def _parse_origin(origin: str, expected_port: int) -> str:
    """Return the canonical host from one exact HTTP origin."""
    if (
        not origin
        or origin == "null"
        or "," in origin
        or any(character.isspace() for character in origin)
    ):
        raise ValueError("Invalid Origin.")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Invalid Origin.") from error
    if (
        parsed.scheme != "http"
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or port != expected_port
    ):
        raise ValueError("Origin does not match the local HTTP service.")
    return _parse_authority(parsed.netloc, expected_port)


def create_app(
    service: NexTuxLinqService,
    *,
    allowed_hosts: set[str] | None = None,
    configured_port: int = 8765,
    service_status_provider: Callable[[], UserServiceStatus] | None = None,
    runtime_mode: str = "unknown",
) -> FastAPI:
    """Create an HTTP application with no hardware or lifecycle ownership."""
    hosts = (
        {"127.0.0.1", "localhost", "::1"}
        if allowed_hosts is None
        else {host.lower() for host in allowed_hosts}
    )
    if service_status_provider is None:
        service_status_provider = lambda: UserServiceStatus(
            runtime_mode=runtime_mode,
            installed=None,
            managed=None,
            enabled=None,
            active=None,
            service_state="unknown",
            openlinkhub_recovery_enabled=None,
            pid=None,
            last_failure_summary=None,
            configuration_path=Path("."),
        )
    app = FastAPI(
        title="NexTuxLinq Matrix900 Controller",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        host_headers = request.headers.getlist("host")
        host_header = host_headers[0] if len(host_headers) == 1 else ""
        try:
            request_host = _parse_authority(host_header, configured_port)
        except ValueError:
            request_host = ""
        if request_host not in hosts:
            return _error(
                request,
                403,
                "invalid_host",
                "The Host header is not allowed.",
            )
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BODY:
                    return _error(
                        request,
                        413,
                        "request_too_large",
                        "The request body exceeds 1 MiB.",
                    )
            except ValueError:
                return _error(
                    request,
                    400,
                    "invalid_content_length",
                    "The Content-Length header is invalid.",
                )
        if request.method in MUTATING_METHODS:
            content_type = request.headers.get("content-type", "")
            if not content_type.lower().startswith("application/json"):
                return _error(
                    request,
                    415,
                    "unsupported_media_type",
                    "JSON requests require application/json.",
                )
            origins = request.headers.getlist("origin")
            if origins:
                origin = origins[0] if len(origins) == 1 else ""
                try:
                    origin_host = _parse_origin(origin, configured_port)
                except ValueError:
                    origin_host = ""
                if origin_host != request_host:
                    return _error(
                        request,
                        403,
                        "invalid_origin",
                        "The request Origin is not allowed.",
                    )
            body = await request.body()
            if len(body) > MAX_REQUEST_BODY:
                return _error(
                    request,
                    413,
                    "request_too_large",
                    "The request body exceeds 1 MiB.",
                )
        try:
            response = await call_next(request)
        except RevisionConflictError:
            return _error(
                request,
                409,
                "revision_conflict",
                "The configuration changed; reload before trying again.",
            )
        except LegacyEditorUnsupportedError:
            return _error(
                request,
                409,
                "legacy_editor_unsupported",
                "This free-layout profile is read-only in the current editor.",
            )
        except CandidateValidationError as error:
            field_errors = []
            for issue in error.issues:
                field = issue.field or "renderer"
                region = issue.region_id or "configuration"
                widget = (
                    issue.widget_type.value
                    if issue.widget_type is not None
                    else None
                )
                field_errors.append({
                    "field": f"regions.{region}.{field}",
                    "message": "The widget settings cannot be rendered.",
                    "region_id": issue.region_id,
                    "widget_type": widget,
                    "category": issue.category,
                    "renderer_field": issue.field,
                    **({"actual_width": issue.actual_width}
                       if issue.actual_width is not None else {}),
                    **({"actual_height": issue.actual_height}
                       if issue.actual_height is not None else {}),
                    **({"minimum_width": issue.minimum_width}
                       if issue.minimum_width is not None else {}),
                    **({"minimum_height": issue.minimum_height}
                       if issue.minimum_height is not None else {}),
                })
            return _error(
                request,
                422,
                "candidate_rendering_failed",
                "The free-layout candidate could not be rendered.",
                field_errors=field_errors,
            )
        except TransferValidationError as error:
            messages = {
                "file_too_large": "The JSON file exceeds the import size limit.",
                "empty_file": "The JSON file is empty.",
                "invalid_utf8": "The file is not valid UTF-8.",
                "malformed_json": "The file does not contain valid JSON.",
                "malformed_upload": "The uploaded file data is invalid.",
                "unsupported_export_format": "The export format is not supported.",
                "unsupported_export_version": "The export format version is not supported.",
                "unsupported_schema_version": "The configuration schema version is not supported.",
                "missing_profile_data": "The export does not contain profile data.",
                "invalid_profile_name": "The exported profile name is invalid.",
                "duplicate_profile_name": "A unique imported profile name is unavailable.",
                "invalid_profile_data": "The imported profile is invalid.",
                "duplicate_region_id": "The imported profile contains a duplicate region ID.",
                "invalid_region_id": "The imported profile contains an invalid region ID.",
                "overlap": "Imported regions overlap.",
                "out_of_bounds": "An imported region is outside the 90 by 10 canvas.",
                "region_limit_reached": "The imported profile exceeds the region limit.",
                "profile_limit_reached": "The imported configuration exceeds the profile limit.",
                "wrong_widget_settings": "Imported widget settings are invalid.",
                "unknown_metric": "The metric identifier is not supported.",
                "unsupported_metric_for_bar": "The selected metric does not support a bar.",
                "invalid_precision": "Metric precision must be between zero and two.",
                "invalid_custom_label": "The custom metric label is invalid.",
                "invalid_bar_range": "The metric bar maximum must exceed its minimum.",
                "invalid_bar_orientation": "The metric bar orientation is invalid.",
                "invalid_bar_direction": "The metric bar direction is invalid.",
                "invalid_configuration": "The imported configuration is invalid.",
                "profile_not_found": "The selected profile was not found.",
            }
            return _error(
                request,
                413 if error.code == "file_too_large" else 404
                if error.code == "profile_not_found" else 422,
                error.code,
                messages.get(error.code, "The import could not be validated."),
                field_errors=[{
                    "field": error.field,
                    "message": "The imported value is not valid for this field.",
                    "category": error.code,
                }],
            )
        except ManagedBackupError as error:
            messages = {
                "backup_not_found": "The selected backup was not found.",
                "invalid_backup": "The selected backup is invalid.",
                "invalid_backup_name": "Use a printable backup name of up to 64 characters.",
                "no_saved_configuration": "There is no saved configuration to back up.",
                "backup_creation_failed": "The backup could not be created.",
                "backup_deletion_failed": "The backup could not be deleted.",
            }
            return _error(
                request,
                404 if error.code == "backup_not_found" else 422,
                error.code,
                messages.get(error.code, "The backup operation could not be completed."),
            )
        except ConfigSaveError:
            return _error(
                request,
                500,
                "persistence_failed",
                "The configuration could not be saved.",
            )
        except ServiceUnavailableError:
            return _error(
                request,
                503,
                "service_unavailable",
                "The NexTuxLinq service is stopping or unavailable.",
            )
        except DomainValidationError as error:
            messages = {
                "unknown_metric": "The metric identifier is not supported.",
                "unsupported_metric_for_bar": "The selected metric does not support a bar.",
                "invalid_precision": "Metric precision must be between zero and two.",
                "invalid_custom_label": "The custom metric label is invalid.",
                "invalid_bar_range": "The metric bar maximum must exceed its minimum.",
                "invalid_bar_orientation": "The metric bar orientation is invalid.",
                "invalid_bar_direction": "The metric bar direction is invalid.",
            }
            return _error(
                request,
                422,
                error.category,
                messages.get(error.category, str(error)),
                field_errors=[
                    {
                        "field": error.field,
                        "message": (
                            "The supplied value is not valid for this field."
                        ),
                    }
                ],
            )
        except ValidationError as error:
            LOGGER.error(
                "Internal API response validation failed (%s).",
                type(error).__name__,
            )
            return _error(
                request,
                500,
                "internal_error",
                "The response could not be generated.",
            )
        except Exception as error:
            LOGGER.error(
                "Unexpected local API request failure (%s).",
                type(error).__name__,
            )
            return _error(
                request,
                500,
                "internal_error",
                "The request could not be completed.",
            )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.scope.get("path", "").startswith(API_PREFIX):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.scope.get("path", "").startswith(API_PREFIX):
            response.headers["Cache-Control"] = "no-store"
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        malformed = any(item["type"] == "json_invalid" for item in error.errors())
        fields = [
            {
                "field": ".".join(str(part) for part in item["loc"][1:]),
                "message": item["msg"],
            }
            for item in error.errors()
        ]
        return _error(
            request,
            400 if malformed else 422,
            "malformed_json" if malformed else "validation_failed",
            "The JSON body is malformed."
            if malformed
            else "The request contains invalid fields.",
            field_errors=fields,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request,
        error: StarletteHTTPException,
    ):
        if request.scope.get("path", "").startswith(API_PREFIX):
            code = "not_found" if error.status_code == 404 else "http_error"
            if error.status_code == 405:
                code = "method_not_allowed"
            return _error(
                request,
                error.status_code,
                code,
                "The API route was not found."
                if error.status_code == 404
                else "The HTTP method is not allowed."
                if error.status_code == 405
                else "The HTTP request could not be completed.",
            )
        return JSONResponse(
            {"detail": "Not Found" if error.status_code == 404 else "HTTP error"},
            status_code=error.status_code,
        )

    @app.exception_handler(ResponseValidationError)
    async def response_validation_error(
        request: Request,
        error: ResponseValidationError,
    ):
        LOGGER.error(
            "Internal API response validation failed (%s).",
            type(error).__name__,
        )
        return _error(
            request,
            500,
            "internal_error",
            "The response could not be generated.",
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, "index.html", {})

    @app.get("/static/app.css", include_in_schema=False)
    async def stylesheet() -> Response:
        return Response(
            (PACKAGE_DIRECTORY / "static" / "app.css").read_bytes(),
            media_type="text/css",
        )

    @app.get("/static/app.js", include_in_schema=False)
    async def javascript() -> Response:
        return Response(
            (PACKAGE_DIRECTORY / "static" / "app.js").read_bytes(),
            media_type="text/javascript",
        )

    @app.get("/static/free_layout_editor.js", include_in_schema=False)
    async def free_layout_javascript() -> Response:
        return Response(
            (
                PACKAGE_DIRECTORY / "static" / "free_layout_editor.js"
            ).read_bytes(),
            media_type="text/javascript",
        )

    @app.get(f"{API_PREFIX}/status", response_model=StatusResponse)
    async def status() -> StatusResponse:
        state = service.snapshot()
        configuration = service.get_configuration()
        user_service = service_status_provider()
        return StatusResponse(
            daemon_state=state.lifecycle_state,
            runtime_mode=user_service.runtime_mode,
            service_installed=user_service.installed,
            service_enabled=user_service.enabled,
            service_active=user_service.active,
            service_state=user_service.service_state,
            openlinkhub_recovery_enabled=(
                user_service.openlinkhub_recovery_enabled
            ),
            device_available=state.device_available,
            model_name="Phanteks Matrix900" if state.device_available else None,
            device_path=state.device_path,
            usb_vid="2e3c",
            usb_pid="6500",
            multiple_device_warning=state.multiple_device_warning,
            active_profile=configuration.configuration.active_profile,
            active_revision=configuration.active_revision,
            saved_revision=configuration.saved_revision,
            dirty=configuration.dirty,
            rendering_state=state.rendering_state,
            update_policy=state.update_policy,
            frame_rate=service.frame_rate,
            last_frame_generation_time=state.last_frame_generation_time,
            last_successful_upload_time=state.last_successful_upload_time,
            last_display_error=state.last_display_error,
            configuration_warning=state.configuration_warning,
        )

    @app.get(f"{API_PREFIX}/config", response_model=ConfigResponse)
    async def config() -> ConfigResponse:
        snapshot = service.get_configuration()
        return ConfigResponse(
            configuration=(
                ConfigurationModel.from_domain(
                    snapshot.legacy_configuration
                )
                if snapshot.legacy_configuration is not None
                else None
            ),
            built_in_default=ConfigurationModel.from_domain(
                snapshot.legacy_built_in_default
            ),
            canonical_configuration=snapshot.configuration.to_dict(),
            legacy_editor_supported=snapshot.legacy_editor_supported,
            legacy_editor_message=(
                None
                if snapshot.legacy_editor_supported
                else (
                    "This free-layout profile is read-only in the current "
                    "editor."
                )
            ),
            active_revision=snapshot.active_revision,
            saved_revision=snapshot.saved_revision,
            dirty=snapshot.dirty,
        )

    @app.post(f"{API_PREFIX}/apply", response_model=ApplyResponse)
    async def apply(request: ApplyRequest) -> ApplyResponse:
        result = await service.apply_profile(
            request.profile_name,
            request.profile.to_domain(),
            expected_revision=request.expected_revision,
        )
        return ApplyResponse(
            profile_name=result.profile_name,
            profile=ProfileModel.from_domain(result.profile),
            active_revision=result.active_revision,
            dirty=result.dirty,
        )

    @app.put(f"{API_PREFIX}/config", response_model=SaveResponse)
    async def save(request: SaveRequest) -> SaveResponse:
        result = await service.save_configuration(
            request.configuration.to_domain(),
            expected_active_revision=request.expected_active_revision,
            expected_saved_revision=request.expected_saved_revision,
            apply=request.apply,
        )
        return SaveResponse(
            configuration=ConfigurationModel.from_domain(result.configuration),
            active_revision=result.active_revision,
            saved_revision=result.saved_revision,
            dirty=result.dirty,
            warning=result.warning,
        )

    @app.get(f"{API_PREFIX}/preview", response_model=PreviewResponse)
    async def preview() -> PreviewResponse:
        snapshot = service.get_preview()
        regions: list[PreviewRegion] = []
        configuration = service.get_configuration()
        if configuration.legacy_configuration is not None:
            layout = configuration.legacy_configuration.active.layout
            widths = (
                ("Front clock", layout.front_clock_width, "clock"),
                ("Gap", layout.gap_width, "gap"),
                (
                    "Scrolling text",
                    layout.scroll_text_width,
                    "scrolling-text",
                ),
                ("Rear clock", layout.rear_clock_width, "clock"),
            )
            start = 0
            for name, width, region_type in widths:
                regions.append(
                    PreviewRegion(
                        name=name,
                        start_pixel=start,
                        width=width,
                        type=region_type,
                    )
                )
                start += width
        else:
            for region in snapshot.profile.regions:
                regions.append(
                    PreviewRegion(
                        id=region.id,
                        name=region.name or region.id,
                        start_pixel=region.x,
                        y=region.y,
                        width=region.width,
                        height=region.height,
                        enabled=region.enabled,
                        background_color=region.background_color,
                        type=region.widget.type.value.replace("_", "-"),
                        widget_type=region.widget.type.value,
                    )
                )
        return PreviewResponse(
            width=MATRIX_WIDTH,
            height=MATRIX_HEIGHT,
            pixel_format="rgb888",
            orientation="physical-front-to-rear",
            framebuffer_base64=base64.b64encode(snapshot.framebuffer).decode("ascii"),
            active_revision=snapshot.active_revision,
            generation_timestamp=snapshot.generated_at,
            regions=regions,
            update_policy=snapshot.render_result.update_policy.value,
            animated_content=snapshot.render_result.animated_content,
        )

    @app.get(
        f"{API_PREFIX}/free-layout/config",
        response_model=CanonicalConfigResponse,
    )
    async def canonical_config() -> CanonicalConfigResponse:
        snapshot = service.get_configuration()
        return CanonicalConfigResponse(
            configuration=CanonicalConfigurationModel.from_domain(
                snapshot.configuration
            ),
            built_in_default=CanonicalConfigurationModel.from_domain(
                snapshot.built_in_default
            ),
            active_revision=snapshot.active_revision,
            saved_revision=snapshot.saved_revision,
            dirty=snapshot.dirty,
            migration_pending=snapshot.migration_pending,
            legacy_editor_supported=snapshot.legacy_editor_supported,
        )

    @app.get(
        f"{API_PREFIX}/free-layout/metadata",
        response_model=FreeLayoutMetadataResponse,
    )
    async def free_layout_metadata() -> FreeLayoutMetadataResponse:
        diagnostics = service.get_metric_diagnostics()
        metric_status = service.get_metric_status()
        return FreeLayoutMetadataResponse(
            canvas_width=MATRIX_WIDTH,
            canvas_height=MATRIX_HEIGHT,
            max_profiles=MAX_PROFILES,
            max_regions_per_profile=MAX_REGIONS_PER_PROFILE,
            max_regions_per_document=MAX_REGIONS_PER_DOCUMENT,
            max_import_bytes=MAX_IMPORT_BYTES,
            widget_definitions=[
                WidgetEditorDefinition(
                    type=definition.widget_type.value,
                    label=definition.display_label,
                    default_settings=definition.default_settings.to_dict(),
                    minimum_width=definition.minimum_width,
                    minimum_height=definition.minimum_height,
                )
                for definition in service.get_widget_definitions()
            ],
            font_definitions=[
                FontEditorDefinition(
                    id="auto",
                    label="Auto — largest fitting",
                    glyph_height=None,
                    maximum_glyph_width=None,
                ),
                *(
                    FontEditorDefinition(
                        id=definition.font_id.value,
                        label=definition.font_id.value.replace("x", "×"),
                        glyph_height=definition.glyph_height,
                        maximum_glyph_width=max(
                            glyph.width for glyph in definition.glyphs.values()
                        ),
                    )
                    for definition in service.get_font_definitions()
                ),
            ],
            metric_definitions=[
                MetricEditorDefinition(
                    id=metric_id.value,
                    label=definition.label,
                    value_type=definition.value_type.value,
                    unit=definition.unit,
                    default_precision=definition.default_precision,
                    supports_bar=definition.supports_bar,
                    default_minimum=definition.bar_minimum,
                    default_maximum=definition.bar_maximum,
                    availability=metric_status[metric_id.value],
                    availability_reason=diagnostics[metric_id.value][1],
                )
                for metric_id, definition in METRIC_DEFINITIONS.items()
            ],
            bar_metric_ids=[
                metric_id.value for metric_id in MetricId
                if metric_id in BAR_METRICS
            ],
        )

    @app.get(
        f"{API_PREFIX}/free-layout/metrics",
        response_model=MetricDiagnosticsResponse,
    )
    async def metric_diagnostics() -> MetricDiagnosticsResponse:
        diagnostics = service.get_metric_diagnostics()
        return MetricDiagnosticsResponse(metrics=[
            MetricDiagnostic(
                id=metric_id.value,
                label=definition.label,
                state=diagnostics[metric_id.value][0],
                reason=diagnostics[metric_id.value][1],
            )
            for metric_id, definition in METRIC_DEFINITIONS.items()
        ])

    def backup_response(backup: ManagedBackup) -> BackupMetadataResponse:
        return BackupMetadataResponse(
            id=backup.id,
            name=backup.name,
            created_at=backup.created_at,
            profile_count=backup.profile_count,
            active_profile=backup.active_profile,
            schema_version=backup.schema_version,
        )

    def import_summary(
        configuration: ConfigurationDocumentV2,
    ) -> ConfigurationImportSummary:
        return ConfigurationImportSummary(
            profile_count=len(configuration.profiles),
            active_profile=configuration.active_profile,
            schema_version=configuration.schema_version,
            replaces_existing_profiles=True,
        )

    @app.get(
        f"{API_PREFIX}/free-layout/presets",
        response_model=PresetListResponse,
    )
    async def presets() -> PresetListResponse:
        response: list[PresetResponse] = []
        for preset in get_builtin_presets():
            document = ConfigurationDocumentV2(
                schema_version=2,
                active_profile="preset",
                profiles={"preset": preset.profile},
            )
            rendered = service.validate_configuration(document)
            response.append(PresetResponse(
                id=preset.id,
                name=preset.name,
                description=preset.description,
                region_count=len(preset.profile.regions),
                profile=preset.profile.to_dict(),
                framebuffer_base64=base64.b64encode(
                    rendered.logical_framebuffer
                ).decode("ascii"),
            ))
        return PresetListResponse(presets=response)

    @app.get(f"{API_PREFIX}/free-layout/export/profile/{{profile_name}}")
    async def export_selected_profile(profile_name: str) -> Response:
        configuration = service.get_configuration().configuration
        profile = configuration.profiles.get(profile_name)
        if profile is None:
            raise TransferValidationError(
                "profile_not_found", field="profile_name"
            ) from None
        service.validate_configuration(ConfigurationDocumentV2(
            schema_version=2,
            active_profile=profile_name,
            profiles={profile_name: profile},
        ))
        filename = safe_profile_filename(profile_name)
        return Response(
            deterministic_json(export_profile(profile_name, profile)),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post(f"{API_PREFIX}/free-layout/export/profile")
    async def export_draft_profile(request: ProfileExportRequest) -> Response:
        profile = request.profile.to_domain()
        service.validate_configuration(ConfigurationDocumentV2(
            schema_version=2,
            active_profile=request.profile_name,
            profiles={request.profile_name: profile},
        ))
        filename = safe_profile_filename(request.profile_name)
        return Response(
            deterministic_json(export_profile(request.profile_name, profile)),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get(f"{API_PREFIX}/free-layout/export/configuration")
    async def export_complete_configuration() -> Response:
        configuration = service.get_configuration().configuration
        service.validate_configuration(configuration)
        return Response(
            deterministic_json(configuration.to_dict()),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="nextuxlinq-config-v2.json"'
            },
        )

    @app.post(
        f"{API_PREFIX}/free-layout/import/profile/validate",
        response_model=ProfileImportResponse,
    )
    async def validate_profile_import(
        request: ProfileImportRequest,
    ) -> ProfileImportResponse:
        raw = parse_json_bytes(decode_import_content(request.content_base64))
        existing = tuple(dict.fromkeys(
            [*service.get_configuration().configuration.profiles, *request.existing_profile_names]
        ))
        imported = import_profile(raw, existing)
        if len(existing) >= MAX_PROFILES:
            raise TransferValidationError(
                "profile_limit_reached", field="existing_profile_names"
            )
        current_regions = sum(
            len(profile.regions)
            for profile in service.get_configuration().configuration.profiles.values()
        )
        existing_regions = max(current_regions, request.existing_region_count)
        if existing_regions + len(imported.profile.regions) > MAX_REGIONS_PER_DOCUMENT:
            raise TransferValidationError(
                "region_limit_reached", field="profile.regions"
            )
        document = ConfigurationDocumentV2(
            schema_version=2,
            active_profile=imported.profile_name,
            profiles={imported.profile_name: imported.profile},
        )
        rendered = service.validate_configuration(document)
        return ProfileImportResponse(
            valid=True,
            profile_name=imported.profile_name,
            profile=imported.profile.to_dict(),
            region_count=len(imported.profile.regions),
            framebuffer_base64=base64.b64encode(
                rendered.logical_framebuffer
            ).decode("ascii"),
        )

    @app.post(
        f"{API_PREFIX}/free-layout/import/configuration/validate",
        response_model=ConfigurationImportResponse,
    )
    async def validate_configuration_import(
        request: ImportFileRequest,
    ) -> ConfigurationImportResponse:
        configuration = import_configuration(
            parse_json_bytes(decode_import_content(request.content_base64))
        )
        rendered = service.validate_configuration(configuration)
        return ConfigurationImportResponse(
            valid=True,
            configuration=CanonicalConfigurationModel.from_domain(configuration),
            summary=import_summary(configuration),
            framebuffer_base64=base64.b64encode(
                rendered.logical_framebuffer
            ).decode("ascii"),
        )

    @app.get(
        f"{API_PREFIX}/free-layout/backups",
        response_model=BackupListResponse,
    )
    async def list_backups() -> BackupListResponse:
        return BackupListResponse(
            retention_limit=MANAGED_BACKUP_RETENTION,
            backups=[backup_response(item) for item in service.list_backups()],
        )

    @app.post(
        f"{API_PREFIX}/free-layout/backups",
        response_model=BackupMetadataResponse,
    )
    async def create_backup(request: BackupCreateRequest) -> BackupMetadataResponse:
        return backup_response(service.create_backup(request.name))

    @app.post(
        f"{API_PREFIX}/free-layout/backups/{{backup_id}}/restore",
        response_model=BackupRestoreResponse,
    )
    async def restore_backup(backup_id: str) -> BackupRestoreResponse:
        configuration = service.restore_backup(backup_id)
        rendered = service.validate_configuration(configuration)
        return BackupRestoreResponse(
            valid=True,
            configuration=CanonicalConfigurationModel.from_domain(configuration),
            summary=import_summary(configuration),
            framebuffer_base64=base64.b64encode(
                rendered.logical_framebuffer
            ).decode("ascii"),
        )

    @app.delete(
        f"{API_PREFIX}/free-layout/backups/{{backup_id}}",
        response_model=DeleteResponse,
    )
    async def delete_backup(backup_id: str) -> DeleteResponse:
        service.delete_backup(backup_id)
        return DeleteResponse(deleted=True)

    @app.post(
        f"{API_PREFIX}/free-layout/validate",
        response_model=CanonicalValidateResponse,
    )
    async def validate_candidate(
        request: CanonicalValidateRequest,
    ) -> CanonicalValidateResponse:
        configuration = request.configuration.to_domain()
        result = service.validate_configuration(configuration)
        resolved_fonts, font_options = service.get_font_validation(
            configuration.active
        )
        return CanonicalValidateResponse(
            valid=True,
            update_policy=result.update_policy.value,
            animated_content=result.animated_content,
            framebuffer_base64=base64.b64encode(
                result.logical_framebuffer
            ).decode("ascii"),
            resolved_fonts=resolved_fonts,
            font_options=font_options,
        )

    @app.post(
        f"{API_PREFIX}/free-layout/apply",
        response_model=CanonicalApplyResponse,
    )
    async def apply_candidate(
        request: CanonicalApplyRequest,
    ) -> CanonicalApplyResponse:
        result = await service.apply_configuration(
            request.configuration.to_domain(),
            expected_revision=request.expected_revision,
        )
        return CanonicalApplyResponse(
            configuration=CanonicalConfigurationModel.from_domain(
                result.configuration
            ),
            active_revision=result.active_revision,
            dirty=result.dirty,
            update_policy=result.render_result.update_policy.value,
        )

    @app.put(
        f"{API_PREFIX}/free-layout/config",
        response_model=CanonicalSaveResponse,
    )
    async def save_candidate(
        request: CanonicalSaveRequest,
    ) -> CanonicalSaveResponse:
        result = await service.save_canonical_configuration(
            request.configuration.to_domain(),
            expected_active_revision=request.expected_active_revision,
            expected_saved_revision=request.expected_saved_revision,
            apply=request.apply,
        )
        return CanonicalSaveResponse(
            configuration=CanonicalConfigurationModel.from_domain(
                result.configuration
            ),
            active_revision=result.active_revision,
            saved_revision=result.saved_revision,
            dirty=result.dirty,
            migration_pending=result.migration_pending,
            warning=result.warning,
        )

    @app.post(
        f"{API_PREFIX}/free-layout/profile",
        response_model=CanonicalApplyResponse,
    )
    async def select_canonical_profile(
        request: ProfileSelectionRequest,
    ) -> CanonicalApplyResponse:
        result = await service.select_profile(
            request.profile_name,
            expected_revision=request.expected_revision,
        )
        return CanonicalApplyResponse(
            configuration=CanonicalConfigurationModel.from_domain(
                result.configuration
            ),
            active_revision=result.active_revision,
            dirty=result.dirty,
            update_policy=result.render_result.update_policy.value,
        )

    return app
