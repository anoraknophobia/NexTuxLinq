"""Tests for the initial foreground nexlinqd entry point."""

import asyncio
from pathlib import Path

import pytest

from nexlinq.config_store import ConfigLoadResult
from nexlinq.daemon import build_parser, run_service, wait_for_shutdown
from nexlinq.defaults import default_configuration
from nexlinq.device_worker import DeviceWorker
from nexlinq.service import NexLinqService
from nexlinq.writer_lock import WriterLock


def test_daemon_parser_supports_foreground_mode_only() -> None:
    arguments = build_parser().parse_args(["--foreground"])
    assert arguments.foreground is True


def test_wait_for_shutdown_returns_for_normal_stop_request() -> None:
    async def scenario() -> None:
        async def block() -> None:
            await asyncio.Future()

        class Service:
            render_task = asyncio.create_task(block())

        stop = asyncio.Event()
        stop.set()
        await wait_for_shutdown(Service(), stop)  # type: ignore[arg-type]
        Service.render_task.cancel()

    asyncio.run(scenario())


def test_wait_for_shutdown_propagates_renderer_failure() -> None:
    async def scenario() -> None:
        async def fail() -> None:
            raise RuntimeError("renderer failed")

        class Service:
            render_task = asyncio.create_task(fail())

        with pytest.raises(RuntimeError, match="renderer failed"):
            await wait_for_shutdown(Service(), asyncio.Event())  # type: ignore[arg-type]

    asyncio.run(scenario())


def test_run_service_renderer_failure_cleans_up_and_returns_nonzero(
    tmp_path: Path,
    caplog,
) -> None:
    class Store:
        def load(self) -> ConfigLoadResult:
            return ConfigLoadResult(default_configuration())

    async def scenario() -> tuple[int, DeviceWorker, Path]:
        lock_path = tmp_path / "matrix900.lock"
        worker = DeviceWorker(
            writer_lock=WriterLock(lock_path),
            discover=lambda: [],
            connection_factory=lambda path: (_ for _ in ()).throw(AssertionError()),
            waiter=lambda delay, stop: stop.wait(1000),
            reconnect_delays=(0.0,),
        )

        async def fail_render_sleep(delay: float) -> None:
            raise RuntimeError("/private/renderer/detail")

        service = NexLinqService(
            config_store=Store(),  # type: ignore[arg-type]
            device_worker=worker,
            sleep=fail_render_sleep,
        )
        result = await run_service(
            service_factory=lambda: service,
            stop_requested=asyncio.Event(),
        )
        return result, worker, lock_path

    result, worker, lock_path = asyncio.run(scenario())

    assert result != 0
    assert not worker.snapshot().running
    replacement = WriterLock(lock_path)
    replacement.acquire()
    replacement.release()
    messages = [record.getMessage() for record in caplog.records]
    assert "NexLinq Matrix900 rendering failed." in messages
    assert all("/private/renderer/detail" not in message for message in messages)
