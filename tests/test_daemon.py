"""Tests for the initial foreground nexlinqd entry point."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from nexlinq.config_store import ConfigLoadResult
from nexlinq.daemon import (
    build_parser,
    run_service,
    validate_host,
    validate_port,
    wait_for_shutdown,
)
from nexlinq.defaults import default_configuration
from nexlinq.device_worker import DeviceWorker
from nexlinq.service import NexLinqService
from nexlinq.state import DaemonSnapshot
from nexlinq.writer_lock import WriterLock


def test_daemon_parser_supports_foreground_mode_only() -> None:
    arguments = build_parser().parse_args(
        ["--foreground", "--host", "::1", "--port", "9000"]
    )
    assert arguments.foreground is True
    assert arguments.host == "::1"
    assert arguments.port == 9000


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    assert validate_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.4"])
def test_non_loopback_hosts_are_rejected(host: str) -> None:
    with pytest.raises(Exception, match="loopback"):
        validate_host(host)


def test_hostname_resolving_to_non_loopback_is_rejected() -> None:
    with (
        patch(
            "socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("192.168.1.10", 0)),
            ],
        ),
        pytest.raises(Exception, match="loopback"),
    ):
        validate_host("matrix900.local")


@pytest.mark.parametrize("port", [0, 65536, "invalid"])
def test_invalid_ports_are_rejected(port) -> None:
    with pytest.raises(Exception, match="Port"):
        validate_port(port)


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

        class Server:
            should_exit = False

            async def serve(self) -> None:
                while not self.should_exit:
                    await asyncio.sleep(0)

        result = await run_service(
            service_factory=lambda: service,
            stop_requested=asyncio.Event(),
            server_factory=lambda app, host, port: Server(),
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


def test_http_startup_failure_stops_service_and_releases_lock(
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

        async def block(delay: float) -> None:
            await asyncio.Future()

        service = NexLinqService(
            config_store=Store(),  # type: ignore[arg-type]
            device_worker=worker,
            sleep=block,
        )

        class FailedServer:
            should_exit = False

            async def serve(self) -> None:
                raise OSError("/private/bind/detail")

        result = await run_service(
            service_factory=lambda: service,
            stop_requested=asyncio.Event(),
            server_factory=lambda app, host, port: FailedServer(),
        )
        return result, worker, lock_path

    result, worker, lock_path = asyncio.run(scenario())
    assert result != 0
    assert not worker.snapshot().running
    replacement = WriterLock(lock_path)
    replacement.acquire()
    replacement.release()
    messages = [record.getMessage() for record in caplog.records]
    assert "NexLinq Matrix900 service failed." in messages
    assert all("/private/bind/detail" not in message for message in messages)


def test_normal_http_shutdown_stops_service_and_releases_lock(
    tmp_path: Path,
) -> None:
    class Store:
        def load(self) -> ConfigLoadResult:
            return ConfigLoadResult(default_configuration())

    async def scenario() -> tuple[int, DeviceWorker, Path, bool]:
        lock_path = tmp_path / "matrix900.lock"
        worker = DeviceWorker(
            writer_lock=WriterLock(lock_path),
            discover=lambda: [],
            connection_factory=lambda path: (_ for _ in ()).throw(AssertionError()),
            waiter=lambda delay, stop: stop.wait(1000),
            reconnect_delays=(0.0,),
        )

        async def block(delay: float) -> None:
            await asyncio.Future()

        service = NexLinqService(
            config_store=Store(),  # type: ignore[arg-type]
            device_worker=worker,
            sleep=block,
        )
        stop_requested = asyncio.Event()

        class Server:
            should_exit = False
            started = asyncio.Event()
            stopped = False

            async def serve(self) -> None:
                self.started.set()
                while not self.should_exit:
                    await asyncio.sleep(0)
                self.stopped = True

        server = Server()
        running = asyncio.create_task(
            run_service(
                service_factory=lambda: service,
                stop_requested=stop_requested,
                server_factory=lambda app, host, port: server,
            )
        )
        await asyncio.wait_for(server.started.wait(), 1)
        stop_requested.set()
        result = await asyncio.wait_for(running, 1)
        return result, worker, lock_path, server.stopped

    result, worker, lock_path, server_stopped = asyncio.run(scenario())
    assert result == 0
    assert server_stopped
    assert not worker.snapshot().running
    replacement = WriterLock(lock_path)
    replacement.acquire()
    replacement.release()


@pytest.mark.parametrize("simultaneous_stop", [False, True])
def test_http_server_failure_is_nonzero_even_with_simultaneous_stop(
    simultaneous_stop: bool,
) -> None:
    async def scenario() -> tuple[int, bool]:
        stop_requested = asyncio.Event()

        class Service:
            render_task = None
            stopped = False

            async def start(self) -> None:
                async def render() -> None:
                    await asyncio.Future()

                self.render_task = asyncio.create_task(render())

            async def wait_for_worker_failure(self) -> None:
                await asyncio.Future()

            def snapshot(self) -> DaemonSnapshot:
                return DaemonSnapshot(
                    lifecycle_state="running",
                    rendering_state="rendering",
                )

            @property
            def owns_resources(self) -> bool:
                return self.render_task is not None

            async def stop(self) -> None:
                self.stopped = True
                assert self.render_task is not None
                self.render_task.cancel()
                try:
                    await self.render_task
                except asyncio.CancelledError:
                    pass
                self.render_task = None

        service = Service()

        class FailedServer:
            should_exit = False

            async def serve(self) -> None:
                if simultaneous_stop:
                    stop_requested.set()
                    await asyncio.sleep(0)
                raise OSError("private simultaneous bind failure")

        result = await run_service(
            service_factory=lambda: service,  # type: ignore[arg-type]
            stop_requested=stop_requested,
            server_factory=lambda app, host, port: FailedServer(),
        )
        return result, service.stopped

    result, stopped = asyncio.run(scenario())
    assert result == 1
    assert stopped


def test_unexpected_clean_http_server_exit_is_nonzero() -> None:
    async def scenario() -> int:
        class Service:
            render_task = None

            async def start(self) -> None:
                async def render() -> None:
                    await asyncio.Future()

                self.render_task = asyncio.create_task(render())

            async def wait_for_worker_failure(self) -> None:
                await asyncio.Future()

            def snapshot(self) -> DaemonSnapshot:
                return DaemonSnapshot(
                    lifecycle_state="running",
                    rendering_state="rendering",
                )

            @property
            def owns_resources(self) -> bool:
                return self.render_task is not None

            async def stop(self) -> None:
                assert self.render_task is not None
                self.render_task.cancel()
                try:
                    await self.render_task
                except asyncio.CancelledError:
                    pass
                self.render_task = None

        class CleanEarlyServer:
            should_exit = False

            async def serve(self) -> None:
                return None

        return await run_service(
            service_factory=Service,  # type: ignore[arg-type]
            stop_requested=asyncio.Event(),
            server_factory=lambda app, host, port: CleanEarlyServer(),
        )

    assert asyncio.run(scenario()) == 1
