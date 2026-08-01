"""Hardware- and systemd-independent recovery diagnostic tests."""

from __future__ import annotations

from types import SimpleNamespace

from nextuxlinq.openlinkhub_recovery import recovery_result
from nextuxlinq.service_manager import OPENLINKHUB_SERVICE_NAME


def result(returncode: int, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="private")


def test_recovery_diagnostic_reports_active_without_exposing_runner_output() -> None:
    calls: list[list[str]] = []

    def runner(arguments: list[str]):
        calls.append(arguments)
        return result(0, "active\nprivate data")

    message = recovery_result(runner)
    assert message == "OpenLinkHub recovery result: active after try-restart."
    assert calls == [[
        "systemctl", "--user", "is-active", OPENLINKHUB_SERVICE_NAME,
    ]]
    assert "private" not in message


def test_recovery_diagnostic_distinguishes_inactive_and_nonfatal_failure() -> None:
    assert recovery_result(lambda arguments: result(3, "inactive\n")) == (
        "OpenLinkHub recovery result: inactive and was not started."
    )
    assert recovery_result(lambda arguments: result(1, "failed\n")) == (
        "OpenLinkHub recovery result: unavailable or failed; failure is nonfatal."
    )


def test_recovery_diagnostic_sanitizes_runner_failure() -> None:
    def fail(arguments: list[str]):
        raise OSError("/private/systemctl detail")

    message = recovery_result(fail)
    assert message == (
        "OpenLinkHub recovery result: status unavailable; failure is nonfatal."
    )
    assert "/private" not in message
