"""Loopback-only FastAPI application for the Matrix900 service."""

from __future__ import annotations

import base64
import ipaddress
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exceptions import ResponseValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from nexlinq.api_models import (
    ApplyRequest,
    ApplyResponse,
    ConfigResponse,
    ConfigurationModel,
    DomainValidationError,
    ErrorResponse,
    PreviewRegion,
    PreviewResponse,
    ProfileModel,
    SaveRequest,
    SaveResponse,
    StatusResponse,
)
from nexlinq.config_store import ConfigSaveError
from nexlinq.creator import MATRIX_HEIGHT, MATRIX_WIDTH
from nexlinq.service import (
    NexLinqService,
    RevisionConflictError,
    ServiceUnavailableError,
)

LOGGER = logging.getLogger(__name__)
API_PREFIX = "/api/v1"
MAX_REQUEST_BODY = 32 * 1024
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
    service: NexLinqService,
    *,
    allowed_hosts: set[str] | None = None,
    configured_port: int = 8765,
) -> FastAPI:
    """Create an HTTP application with no hardware or lifecycle ownership."""
    hosts = (
        {"127.0.0.1", "localhost", "::1"}
        if allowed_hosts is None
        else {host.lower() for host in allowed_hosts}
    )
    app = FastAPI(
        title="NexLinq Matrix900 Controller",
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
                        "The request body exceeds 32 KiB.",
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
                    "The request body exceeds 32 KiB.",
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
                "The NexLinq service is stopping or unavailable.",
            )
        except DomainValidationError as error:
            return _error(
                request,
                422,
                "validation_failed",
                str(error),
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

    @app.get(f"{API_PREFIX}/status", response_model=StatusResponse)
    async def status() -> StatusResponse:
        state = service.snapshot()
        configuration = service.get_configuration()
        return StatusResponse(
            daemon_state=state.lifecycle_state,
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
            configuration=ConfigurationModel.from_domain(snapshot.configuration),
            built_in_default=ConfigurationModel.from_domain(
                snapshot.built_in_default
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
        layout = snapshot.profile.layout
        widths = (
            ("Front clock", layout.front_clock_width, "clock"),
            ("Gap", layout.gap_width, "gap"),
            ("Scrolling text", layout.scroll_text_width, "scrolling-text"),
            ("Rear clock", layout.rear_clock_width, "clock"),
        )
        start = 0
        regions: list[PreviewRegion] = []
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
        return PreviewResponse(
            width=MATRIX_WIDTH,
            height=MATRIX_HEIGHT,
            pixel_format="rgb888",
            orientation="physical-front-to-rear",
            framebuffer_base64=base64.b64encode(snapshot.framebuffer).decode("ascii"),
            active_revision=snapshot.active_revision,
            generation_timestamp=snapshot.generated_at,
            regions=regions,
        )

    return app
