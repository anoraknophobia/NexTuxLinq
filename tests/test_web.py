"""Hardware-independent tests for the local HTTP API and GUI."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx
import pytest

from nexlinq.config_store import ConfigLoadResult, ConfigSaveError
from nexlinq.defaults import default_configuration
from nexlinq.service import NexLinqService
from nexlinq.service import RevisionConflictError
from nexlinq.web import MAX_REQUEST_BODY, create_app


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.saved = []
        self.error: ConfigSaveError | None = None

    def load(self) -> ConfigLoadResult:
        return ConfigLoadResult(default_configuration())

    def save(self, configuration) -> None:
        if self.error is not None:
            raise self.error
        self.saved.append(configuration)


class FakeWorker:
    def __init__(self) -> None:
        self.frames: list[tuple[bytes, int]] = []
        self.brightness: list[tuple[int, int]] = []

    def submit_frame(self, frame: bytes, revision: int = 0) -> None:
        self.frames.append((frame, revision))

    def set_brightness(self, value: int, revision: int = 0) -> None:
        self.brightness.append((value, revision))


def service_for_api(tmp_path: Path) -> tuple[NexLinqService, MemoryStore, FakeWorker]:
    store = MemoryStore(tmp_path / "config.json")
    worker = FakeWorker()
    service = NexLinqService(
        config_store=store,  # type: ignore[arg-type]
        device_worker=worker,  # type: ignore[arg-type]
        monotonic=lambda: 10.0,
    )
    service._configuration = default_configuration()
    service._saved_configuration = default_configuration()
    service._saved_revision = 1
    service._saved_revision_counter = 1
    service._started_at = 0.0
    service.state.update(
        lifecycle_state="running",
        rendering_state="rendering",
        active_configuration_revision=1,
    )
    return service, store, worker


@pytest.fixture
def api_client(tmp_path: Path):
    service, store, worker = service_for_api(tmp_path)
    client = LocalClient(create_app(service))
    yield client, service, store, worker


class LocalClient:
    """Small synchronous facade over HTTPX's in-process ASGI transport."""

    def __init__(self, app) -> None:
        self.app = app

    def request(self, method: str, path: str, **kwargs):
        async def send():
            headers = {"host": "127.0.0.1", **kwargs.pop("headers", {})}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://127.0.0.1",
                headers=headers,
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)


def profile_payload(**changes):
    profile = default_configuration().active.to_dict()
    profile.update(changes)
    return profile


def apply_payload(**profile_changes):
    return {
        "profile_name": "default",
        "profile": profile_payload(**profile_changes),
        "expected_revision": 1,
    }


def test_status_and_config_are_strict_read_only_responses(api_client) -> None:
    client, service, store, worker = api_client
    before = service.get_configuration()
    status = client.get("/api/v1/status")
    config = client.get("/api/v1/config")

    assert status.status_code == 200
    assert status.json()["usb_vid"] == "2e3c"
    assert status.json()["device_available"] is False
    assert config.status_code == 200
    assert config.json()["configuration"]["active_profile"] == "default"
    assert service.get_configuration() == before
    assert store.saved == []
    assert worker.frames == []


def test_apply_normalizes_without_persisting_and_queues_brightness(api_client) -> None:
    client, service, store, worker = api_client
    response = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="matrix 900!", brightness=70),
    )

    assert response.status_code == 200
    assert response.json()["profile"]["scrolling_text"] == "MATRIX 900!"
    assert response.json()["active_revision"] == 2
    assert store.saved == []
    assert worker.frames[-1][1] == 2
    assert worker.brightness == [(70, 2)]
    assert service.configuration.active.scrolling_text == "MATRIX 900!"


def test_apply_with_null_brightness_does_not_write_brightness(api_client) -> None:
    client, _, _, worker = api_client
    response = client.post("/api/v1/apply", json=apply_payload(brightness=None))
    assert response.status_code == 200
    assert worker.brightness == []


def test_save_persists_and_applies(api_client) -> None:
    client, service, store, worker = api_client
    document = default_configuration().to_dict()
    document["profiles"]["default"]["scrolling_text"] = "saved"
    response = client.put(
        "/api/v1/config",
        json={
            "configuration": document,
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["configuration"]["profiles"]["default"][
        "scrolling_text"
    ] == "SAVED"
    assert response.json()["dirty"] is False
    assert len(store.saved) == 1
    assert service.configuration.active.scrolling_text == "SAVED"
    assert worker.frames[-1][1] == 2


def test_apply_false_uses_independent_saved_revisions(api_client) -> None:
    client, service, store, _ = api_client
    first = default_configuration().to_dict()
    first["profiles"]["default"]["scrolling_text"] = "FIRST"
    response_one = client.put(
        "/api/v1/config",
        json={
            "configuration": first,
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": False,
        },
    )
    second = default_configuration().to_dict()
    second["profiles"]["default"]["scrolling_text"] = "SECOND"
    response_two = client.put(
        "/api/v1/config",
        json={
            "configuration": second,
            "expected_active_revision": 1,
            "expected_saved_revision": 2,
            "apply": False,
        },
    )

    assert response_one.status_code == response_two.status_code == 200
    assert response_one.json()["active_revision"] == 1
    assert response_one.json()["saved_revision"] == 2
    assert response_two.json()["active_revision"] == 1
    assert response_two.json()["saved_revision"] == 3
    assert response_two.json()["dirty"] is True
    assert service.configuration.active.scrolling_text == "CACHYOS"
    assert [item.active.scrolling_text for item in store.saved] == [
        "FIRST",
        "SECOND",
    ]

    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="SECOND"),
    )
    assert applied.status_code == 200
    assert applied.json()["active_revision"] == 2
    assert applied.json()["dirty"] is False
    assert client.get("/api/v1/config").json()["saved_revision"] == 3


def test_apply_then_save_without_apply_preserves_active_revision(api_client) -> None:
    client, service, _, _ = api_client
    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="RUNTIME"),
    )
    assert applied.status_code == 200
    document = default_configuration().to_dict()
    document["profiles"]["default"]["scrolling_text"] = "DISK"
    saved = client.put(
        "/api/v1/config",
        json={
            "configuration": document,
            "expected_active_revision": 2,
            "expected_saved_revision": 1,
            "apply": False,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["active_revision"] == 2
    assert saved.json()["saved_revision"] == 2
    assert saved.json()["dirty"] is True
    assert service.configuration.active.scrolling_text == "RUNTIME"


@pytest.mark.parametrize(
    ("active_revision", "saved_revision", "apply"),
    [
        (0, 1, True),
        (1, 0, True),
        (0, 0, True),
        (0, 0, False),
    ],
)
def test_save_rejects_stale_required_revisions(
    api_client,
    active_revision: int,
    saved_revision: int,
    apply: bool,
) -> None:
    client, service, store, worker = api_client
    response = client.put(
        "/api/v1/config",
        json={
            "configuration": default_configuration().to_dict(),
            "expected_active_revision": active_revision,
            "expected_saved_revision": saved_revision,
            "apply": apply,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "revision_conflict"
    assert service.snapshot().active_configuration_revision == 1
    assert store.saved == []
    assert worker.frames == []


def test_apply_false_ignores_active_revision_but_checks_saved(api_client) -> None:
    client, service, store, _ = api_client
    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="NEW RUNTIME"),
    )
    assert applied.status_code == 200
    document = default_configuration().to_dict()
    document["profiles"]["default"]["scrolling_text"] = "DISK ONLY"

    saved = client.put(
        "/api/v1/config",
        json={
            "configuration": document,
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": False,
        },
    )

    assert saved.status_code == 200
    assert saved.json()["active_revision"] == 2
    assert saved.json()["saved_revision"] == 2
    assert saved.json()["dirty"] is True
    assert service.configuration.active.scrolling_text == "NEW RUNTIME"
    assert store.saved[-1].active.scrolling_text == "DISK ONLY"


def test_stale_browser_cannot_save_over_newer_runtime_apply(api_client) -> None:
    client, service, store, _ = api_client
    initial = client.get("/api/v1/config").json()
    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="NEWER"),
    )
    assert applied.status_code == 200

    stale_save = client.put(
        "/api/v1/config",
        json={
            "configuration": initial["configuration"],
            "expected_active_revision": initial["active_revision"],
            "expected_saved_revision": initial["saved_revision"],
            "apply": True,
        },
    )

    assert stale_save.status_code == 409
    assert service.configuration.active.scrolling_text == "NEWER"
    assert store.saved == []


def test_revision_conflict_preserves_state(api_client) -> None:
    client, service, store, worker = api_client
    response = client.post(
        "/api/v1/apply",
        json={**apply_payload(), "expected_revision": 0},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "revision_conflict"
    assert service.snapshot().active_configuration_revision == 1
    assert store.saved == []
    assert worker.frames == []


def test_concurrent_apply_requests_are_revision_serialized(tmp_path: Path) -> None:
    service, _, _ = service_for_api(tmp_path)

    async def scenario():
        return await asyncio.gather(
            service.apply_profile(
                "default",
                default_configuration().active,
                expected_revision=1,
            ),
            service.apply_profile(
                "default",
                default_configuration().active,
                expected_revision=1,
            ),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, RevisionConflictError) for result in results) == 1
    assert service.snapshot().active_configuration_revision == 2


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"scrolling_text": ""}, "scrolling_text"),
        ({"scrolling_text": "SNOWMAN ☃"}, "unsupported"),
        ({"text_color": "turquoise"}, "text_color"),
        ({"scroll_speed": 100}, "scroll_speed"),
        ({"brightness": 55}, "brightness"),
        (
            {
                "layout": {
                    "front_clock_width": 23,
                    "gap_width": 4,
                    "scroll_text_width": 31,
                    "rear_clock_width": 31,
                }
            },
            "width",
        ),
    ],
)
def test_domain_validation_errors_are_422(api_client, changes, field) -> None:
    client, service, _, _ = api_client
    response = client.post("/api/v1/apply", json=apply_payload(**changes))
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    assert field.lower() in response.json()["message"].lower()
    assert service.snapshot().active_configuration_revision == 1


def test_request_parsing_and_security_failures(api_client) -> None:
    client, _, _, _ = api_client
    malformed = client.post(
        "/api/v1/apply",
        content="{",
        headers={"content-type": "application/json"},
    )
    wrong_type = client.post("/api/v1/apply", content="{}")
    unknown = client.post(
        "/api/v1/apply",
        json={**apply_payload(), "unknown": True},
    )
    oversized = client.post(
        "/api/v1/apply",
        content=b"x" * (MAX_REQUEST_BODY + 1),
        headers={"content-type": "application/json"},
    )

    assert malformed.status_code == 400
    assert wrong_type.status_code == 415
    assert unknown.status_code == 422
    assert oversized.status_code == 413
    for response in (malformed, wrong_type, unknown, oversized):
        assert response.json()["request_id"]


def test_host_origin_and_security_headers(api_client) -> None:
    client, _, _, _ = api_client
    invalid_host = client.get(
        "/api/v1/status",
        headers={"host": "matrix900.example"},
    )
    invalid_origin = client.post(
        "/api/v1/apply",
        json=apply_payload(),
        headers={"origin": "http://evil.example"},
    )
    same_origin = client.post(
        "/api/v1/apply",
        json=apply_payload(),
        headers={"origin": "http://127.0.0.1:8765"},
    )

    assert invalid_host.status_code == 403
    assert invalid_origin.status_code == 403
    assert same_origin.status_code == 200
    for response in (invalid_host, invalid_origin, same_origin):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    ("origin", "status"),
    [
        ("http://127.0.0.1:8765", 200),
        ("https://127.0.0.1:8765", 403),
        ("http://127.0.0.1:8765/path", 403),
        ("http://127.0.0.1:8765?query=yes", 403),
        ("http://127.0.0.1:8765#fragment", 403),
        ("http://user@127.0.0.1:8765", 403),
        ("null", 403),
        ("http://127.0.0.1:8765 http://127.0.0.1:8765", 403),
        ("http://127.0.0.1:9000", 403),
    ],
)
def test_origin_requires_exact_http_origin(api_client, origin: str, status: int) -> None:
    client, _, _, _ = api_client
    response = client.post(
        "/api/v1/apply",
        json=apply_payload(),
        headers={"origin": origin},
    )
    assert response.status_code == status


def test_bracketed_ipv6_origin_is_accepted(tmp_path: Path) -> None:
    service, _, _ = service_for_api(tmp_path)
    client = LocalClient(create_app(service))
    response = client.post(
        "/api/v1/apply",
        json=apply_payload(),
        headers={
            "host": "[::1]:8765",
            "origin": "http://[::1]:8765",
        },
    )
    assert response.status_code == 200


def test_multiple_origin_headers_are_rejected(api_client) -> None:
    client, _, _, _ = api_client

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://127.0.0.1",
        ) as async_client:
            return await async_client.post(
                "/api/v1/apply",
                json=apply_payload(),
                headers=[
                    ("host", "127.0.0.1"),
                    ("origin", "http://127.0.0.1:8765"),
                    ("origin", "http://127.0.0.1:8765"),
                ],
            )

    assert asyncio.run(send()).status_code == 403


@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("127.0.0.1", 200),
        ("127.0.0.1:8765", 200),
        ("127.0.0.1:invalid", 403),
        ("127.0.0.1:", 403),
        ("[::1]", 200),
        ("[::1]:8765", 200),
        ("[::1]:invalid", 403),
        ("[::1", 403),
        ("::1", 403),
        ("127.0.0.1:9000", 403),
        ("192.168.1.2:8765", 403),
        ("0.0.0.0:8765", 403),
        ("user@127.0.0.1:8765", 403),
    ],
)
def test_host_authority_is_strict(api_client, host: str, status: int) -> None:
    client, _, _, _ = api_client
    response = client.get("/api/v1/status", headers={"host": host})
    assert response.status_code == status


def test_api_404_405_and_internal_errors_use_stable_shape(api_client) -> None:
    client, service, _, _ = api_client
    not_found = client.get("/api/v1/missing")
    wrong_method = client.put(
        "/api/v1/status",
        json={},
    )
    original = service.get_configuration
    service.get_configuration = lambda: (_ for _ in ()).throw(
        RuntimeError("/private/internal/detail")
    )
    internal = client.get("/api/v1/status")
    service.get_configuration = original

    for response, code in (
        (not_found, "not_found"),
        (wrong_method, "method_not_allowed"),
        (internal, "internal_error"),
    ):
        assert response.json()["code"] == code
        assert response.json()["message"]
        assert response.json()["request_id"]
        assert response.json()["field_errors"] is None
        assert "/private/" not in response.text


@pytest.mark.parametrize(
    ("exception", "secret"),
    [
        (ValueError("/private/config.json"), "/private/config.json"),
        (TypeError("InternalWidget implementation"), "InternalWidget"),
    ],
)
def test_unexpected_value_and_type_errors_are_sanitized(
    api_client,
    exception: Exception,
    secret: str,
) -> None:
    client, service, _, _ = api_client
    original = service.get_configuration
    service.get_configuration = lambda: (_ for _ in ()).throw(exception)
    response = client.get("/api/v1/status")
    service.get_configuration = original

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert response.json()["message"] == "The request could not be completed."
    assert secret not in response.text


def test_preview_uses_physical_renderer_and_exact_regions(api_client) -> None:
    client, _, _, _ = api_client
    response = client.get("/api/v1/preview")
    payload = response.json()
    framebuffer = base64.b64decode(payload["framebuffer_base64"])

    assert response.status_code == 200
    assert (payload["width"], payload["height"]) == (90, 10)
    assert payload["pixel_format"] == "rgb888"
    assert payload["orientation"] == "physical-front-to-rear"
    assert len(framebuffer) == 2700
    assert [region["width"] for region in payload["regions"]] == [24, 4, 31, 31]
    assert [region["start_pixel"] for region in payload["regions"]] == [0, 24, 28, 59]
    assert payload["active_revision"] == 1


def test_preview_custom_colors_stay_in_physical_regions(api_client) -> None:
    client, _, _, _ = api_client
    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(
            scrolling_text="L",
            text_color="#112233",
            clock_color="#445566",
            scroll_speed=0.5,
        ),
    )
    assert applied.status_code == 200
    payload = client.get("/api/v1/preview").json()
    frame = base64.b64decode(payload["framebuffer_base64"])

    def pixel(x: int, y: int) -> bytes:
        offset = (y * 90 + x) * 3
        return frame[offset : offset + 3]

    clock = bytes.fromhex("445566")
    text = bytes.fromhex("112233")
    assert any(pixel(x, y) == clock for x in range(24) for y in range(10))
    assert all(pixel(x, y) == b"\0\0\0" for x in range(24, 28) for y in range(10))
    assert any(pixel(x, y) == text for x in range(28, 59) for y in range(10))
    assert any(pixel(x, y) == clock for x in range(59, 90) for y in range(10))
    assert all(
        pixel(x, y) != text
        for x in list(range(28)) + list(range(59, 90))
        for y in range(10)
    )


def test_precommit_save_failure_changes_no_state(api_client) -> None:
    client, service, store, worker = api_client
    store.error = ConfigSaveError("private filename", committed=False)
    response = client.put(
        "/api/v1/config",
        json={
            "configuration": default_configuration().to_dict(),
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": True,
        },
    )

    assert response.status_code == 500
    assert response.json()["message"] == "The configuration could not be saved."
    assert "private" not in response.text
    assert service.snapshot().active_configuration_revision == 1
    assert worker.frames == []


def test_postcommit_warning_is_truthful_and_publishes_state(api_client) -> None:
    client, service, store, _ = api_client
    store.error = ConfigSaveError("private directory", committed=True)
    response = client.put(
        "/api/v1/config",
        json={
            "configuration": default_configuration().to_dict(),
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": True,
        },
    )

    assert response.status_code == 200
    assert "durability" in response.json()["warning"]
    assert response.json()["saved_revision"] == 2
    assert service.snapshot().configuration_warning == response.json()["warning"]
    assert "private" not in response.text


def test_frontend_and_local_assets_are_self_contained(api_client) -> None:
    client, _, _, _ = api_client

    async def fetch_assets():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://127.0.0.1",
        ) as async_client:
            return (
                await async_client.get("/"),
                await async_client.get("/static/app.css"),
                await async_client.get("/static/app.js"),
            )

    page, css, javascript = asyncio.run(fetch_assets())

    assert page.status_code == css.status_code == javascript.status_code == 200
    assert "NexLinq Matrix900 Controller" in page.text
    assert 'aria-live="polite"' in page.text
    assert "A 90 by 10 pixel preview" in page.text
    assert "https://" not in page.text
    assert "http://" not in javascript.text
    assert "https://" not in javascript.text
    assert "innerHTML" not in javascript.text
    assert "setInterval" not in javascript.text
    assert "setTimeout(pollStatus" in javascript.text
    assert "setTimeout(pollPreview" in javascript.text
    assert 'request("/config")' in javascript.text
    assert "revertRuntimeConfiguration" in javascript.text
    assert 'window.addEventListener("beforeunload"' in javascript.text
