"""Sanitized journal diagnostics for the fixed OpenLinkHub recovery hook."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Protocol

from nextuxlinq.service_manager import OPENLINKHUB_SERVICE_NAME


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], CompletedProcessLike]


def _runner(arguments: list[str]) -> CompletedProcessLike:
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
    )


def recovery_result(runner: Runner = _runner) -> str:
    """Return one safe result after systemd's nonfatal try-restart command."""
    try:
        result = runner([
            "systemctl",
            "--user",
            "is-active",
            OPENLINKHUB_SERVICE_NAME,
        ])
    except (OSError, subprocess.SubprocessError):
        return "OpenLinkHub recovery result: status unavailable; failure is nonfatal."
    state = result.stdout.strip().splitlines()
    current = state[0] if state else "unknown"
    if result.returncode == 0 and current == "active":
        return "OpenLinkHub recovery result: active after try-restart."
    if current == "inactive":
        return "OpenLinkHub recovery result: inactive and was not started."
    return "OpenLinkHub recovery result: unavailable or failed; failure is nonfatal."


def main() -> int:
    print(recovery_result())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
