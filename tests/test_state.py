"""Tests for defensive daemon state snapshots."""

from dataclasses import FrozenInstanceError

import pytest

from nexlinq.state import DaemonState


def test_snapshots_are_immutable_and_updates_replace_them() -> None:
    state = DaemonState()
    first = state.snapshot()
    second = state.update(lifecycle_state="running")

    assert first.lifecycle_state == "stopped"
    assert second.lifecycle_state == "running"
    with pytest.raises(FrozenInstanceError):
        second.device_available = True  # type: ignore[misc]
