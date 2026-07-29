"""Project identity and stale-reference tests for the NexTuxLinq rename."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tomllib
from pathlib import Path

import nextuxlinq
from nextuxlinq.cli import build_parser as build_cli_parser
from nextuxlinq.daemon import build_parser as build_daemon_parser


REPOSITORY = Path(__file__).resolve().parents[1]


def _project_text_files() -> list[Path]:
    files = [
        REPOSITORY / "LICENSE",
        REPOSITORY / "README.md",
        REPOSITORY / "pyproject.toml",
    ]
    for directory in ("docs", "src", "systemd", "tests", "udev"):
        files.extend(
            path
            for path in (REPOSITORY / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    return files


def test_import_package_is_nextuxlinq_only() -> None:
    assert nextuxlinq.__name__ == "nextuxlinq"
    assert Path(nextuxlinq.__file__).parent.name == "nextuxlinq"
    assert not (REPOSITORY / "src" / ("nex" + "linq")).exists()
    assert importlib.util.find_spec("nex" + "linq") is None


def test_distribution_and_executable_entry_points_are_renamed() -> None:
    with (REPOSITORY / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]

    assert project["name"] == "nextuxlinq"
    assert project["scripts"] == {
        "nextuxlinqctl": "nextuxlinq.cli:main",
        "nextuxlinqd": "nextuxlinq.daemon:main",
    }
    assert build_cli_parser().prog == "nextuxlinqctl"
    assert build_daemon_parser().prog == "nextuxlinqd"


def test_generated_executables_use_nextuxlinq_branding() -> None:
    executable_directory = REPOSITORY / ".venv" / "bin"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH"
    }
    for executable_name in ("nextuxlinqctl", "nextuxlinqd"):
        result = subprocess.run(
            [executable_directory / executable_name, "--help"],
            cwd=REPOSITORY,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "NexTuxLinq" in result.stdout

    assert not (executable_directory / ("nex" + "linqctl")).exists()
    assert not (executable_directory / ("nex" + "linqd")).exists()


def test_renamed_udev_rule_preserves_matrix900_match() -> None:
    renamed = REPOSITORY / "udev" / "70-nextuxlinq.rules"
    obsolete = REPOSITORY / "udev" / ("70-nex" + "linq.rules")

    assert renamed.is_file()
    assert not obsolete.exists()
    rule = renamed.read_text(encoding="utf-8")
    assert 'SUBSYSTEM=="hidraw"' in rule
    assert 'ATTRS{idVendor}=="2e3c"' in rule
    assert 'ATTRS{idProduct}=="6500"' in rule
    assert 'TAG+="uaccess"' in rule
    assert 'MODE="0660"' in rule


def test_future_daemon_unit_name_is_reserved_without_integration() -> None:
    documentation = (REPOSITORY / "docs" / "daemon-core.md").read_text(
        encoding="utf-8"
    )

    assert "`nextuxlinqd.service`" in documentation
    assert not (REPOSITORY / "systemd" / "nextuxlinqd.service").exists()


def test_renamed_helpers_conflict_with_other_and_legacy_writers() -> None:
    display = (
        REPOSITORY / "systemd" / "nextuxlinq-display.service"
    ).read_text(encoding="utf-8")
    telemetry = (
        REPOSITORY / "systemd" / "nextuxlinq-telemetry.service"
    ).read_text(encoding="utf-8")
    legacy_display = "nex" + "linq-display.service"
    legacy_telemetry = "nex" + "linq-telemetry.service"

    assert (
        "Conflicts=nextuxlinq-telemetry.service "
        f"{legacy_display} {legacy_telemetry}"
    ) in display
    assert (
        "Conflicts=nextuxlinq-display.service "
        f"{legacy_display} {legacy_telemetry}"
    ) in telemetry


def _classify_legacy_name(relative: str, line: str) -> str | None:
    legacy_name = "nex" + "linq"
    folded = line.casefold()
    official_markers = (
        "phanteks " + legacy_name,
        "official " + legacy_name,
        "other " + legacy_name,
        legacy_name + " are trademarks",
        legacy_name + " layout",
        legacy_name + " brightness",
        "captured `" + legacy_name,
    )
    official_paths = {
        "README.md",
        "pyproject.toml",
        "docs/protocol.md",
        "src/nextuxlinq/cli.py",
        "src/nextuxlinq/protocol.py",
        "tests/test_free_text.py",
        "tests/test_protocol.py",
        "tests/test_reports.py",
    }
    if relative in official_paths and any(
        marker in folded for marker in official_markers
    ):
        return "official Phanteks product or verified protocol"
    if (
        relative == "tests/test_free_text.py"
        and f'"{legacy_name}"' in folded
    ):
        return "verified captured payload"

    if relative in {
        "src/nextuxlinq/config_store.py",
        "tests/test_config_store.py",
        "docs/dynamic-display.md",
    }:
        return "legacy configuration migration"
    if relative == "docs/daemon-core.md":
        if f"/{legacy_name}/config.json" in folded:
            return "legacy configuration migration"
        if f"/{legacy_name}/matrix900.lock" in folded:
            return "legacy writer-lock migration"
        if f"{legacy_name}-display.service" in folded:
            return "legacy service migration"
    if relative in {
        "src/nextuxlinq/writer_lock.py",
        "tests/test_writer_lock.py",
    }:
        return "legacy writer-lock migration"
    if relative.startswith("systemd/"):
        if folded.startswith("conflicts="):
            return "legacy service migration"
        if legacy_name + "-linux" in folded:
            return "local development checkout"
    if relative == "docs/systemd.md":
        if legacy_name + "-linux" in folded:
            return "local development checkout"
        if legacy_name + " helpers" in folded:
            return "legacy service migration"
        if legacy_name + "-display.service" in folded:
            return "legacy service migration"
    return None


def test_every_legacy_name_occurrence_has_an_explicit_classification() -> None:
    needle = ("nex" + "linq").casefold()
    unclassified: list[str] = []
    for path in _project_text_files():
        relative = path.relative_to(REPOSITORY).as_posix()
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if needle in line.casefold() and _classify_legacy_name(
                relative,
                line,
            ) is None:
                unclassified.append(
                    f"{relative}:{number}: {line.strip()}"
                )

    assert unclassified == []


def test_obsolete_project_paths_and_filenames_are_absent() -> None:
    legacy_name = "nex" + "linq"
    forbidden = (
        REPOSITORY / "src" / legacy_name,
        REPOSITORY / "udev" / f"70-{legacy_name}.rules",
        REPOSITORY / "systemd" / f"{legacy_name}-display.service",
        REPOSITORY / "systemd" / f"{legacy_name}-telemetry.service",
    )

    assert [path.relative_to(REPOSITORY) for path in forbidden if path.exists()] == []
