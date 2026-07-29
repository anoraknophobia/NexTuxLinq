"""Tests for direct CLI writer-lock compatibility."""

from pathlib import Path
from unittest.mock import patch

import pytest

from nextuxlinq import cli
from nextuxlinq import daemon
from nextuxlinq import default_display
from nextuxlinq.writer_lock import WriterLock, WriterLockHeldError


def test_direct_mutation_refuses_while_daemon_lock_is_held(
    tmp_path: Path,
    capsys,
) -> None:
    environment = {"XDG_RUNTIME_DIR": str(tmp_path)}
    candidate = WriterLock.from_environment(environment)
    assert candidate.legacy_path is not None
    legacy_lock = WriterLock(candidate.legacy_path, owner="legacy writer")
    legacy_lock.acquire()
    try:
        with (
            patch.dict("os.environ", environment, clear=True),
            patch("sys.argv", ["nextuxlinqctl", "brightness", "50"]),
            patch("nextuxlinq.cli.set_device_brightness") as mutation,
        ):
            result = cli.main()
    finally:
        legacy_lock.release()

    assert result == 1
    mutation.assert_not_called()
    assert "Another NexTuxLinq writer" in capsys.readouterr().err


def test_daemon_factory_uses_the_same_dual_lock(tmp_path: Path) -> None:
    environment = {
        "XDG_RUNTIME_DIR": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    candidate = WriterLock.from_environment(environment)
    assert candidate.legacy_path is not None
    legacy_lock = WriterLock(candidate.legacy_path, owner="legacy writer")
    legacy_lock.acquire()
    try:
        with patch.dict("os.environ", environment, clear=True):
            service = daemon._create_service()
        with pytest.raises(WriterLockHeldError, match="legacy"):
            service.device_worker._writer_lock.acquire()
    finally:
        legacy_lock.release()


def test_legacy_display_reports_lock_contention_without_traceback(
    tmp_path: Path,
    capsys,
) -> None:
    environment = {"XDG_RUNTIME_DIR": str(tmp_path)}
    daemon_lock = WriterLock.from_environment(environment, owner="nextuxlinqd")
    daemon_lock.acquire()
    try:
        with patch.dict("os.environ", environment, clear=True):
            result = default_display.main(["--once"])
    finally:
        daemon_lock.release()

    captured = capsys.readouterr()
    assert result == 1
    assert "Another NexTuxLinq writer" in captured.err
    assert "Traceback" not in captured.err
