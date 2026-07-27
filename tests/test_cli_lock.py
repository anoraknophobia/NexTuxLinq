"""Tests for direct CLI writer-lock compatibility."""

from pathlib import Path
from unittest.mock import patch

from nexlinq import cli
from nexlinq import default_display
from nexlinq.writer_lock import WriterLock


def test_direct_mutation_refuses_while_daemon_lock_is_held(
    tmp_path: Path,
    capsys,
) -> None:
    environment = {"XDG_RUNTIME_DIR": str(tmp_path)}
    daemon_lock = WriterLock.from_environment(environment, owner="nexlinqd")
    daemon_lock.acquire()
    try:
        with (
            patch.dict("os.environ", environment, clear=True),
            patch("sys.argv", ["nexlinqctl", "brightness", "50"]),
            patch("nexlinq.cli.set_device_brightness") as mutation,
        ):
            result = cli.main()
    finally:
        daemon_lock.release()

    assert result == 1
    mutation.assert_not_called()
    assert "Another NexLinq writer" in capsys.readouterr().err


def test_legacy_display_reports_lock_contention_without_traceback(
    tmp_path: Path,
    capsys,
) -> None:
    environment = {"XDG_RUNTIME_DIR": str(tmp_path)}
    daemon_lock = WriterLock.from_environment(environment, owner="nexlinqd")
    daemon_lock.acquire()
    try:
        with patch.dict("os.environ", environment, clear=True):
            result = default_display.main(["--once"])
    finally:
        daemon_lock.release()

    captured = capsys.readouterr()
    assert result == 1
    assert "Another NexLinq writer" in captured.err
    assert "Traceback" not in captured.err
