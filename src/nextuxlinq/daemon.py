"""Foreground entry point for the NexTuxLinq Matrix900 service core."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import signal
import socket
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from typing import Protocol

import uvicorn
from fastapi import FastAPI
from nextuxlinq.config_store import ConfigStore
from nextuxlinq.device_worker import DeviceWorker
from nextuxlinq.service import NexTuxLinqService
from nextuxlinq.service_manager import (
    CachedUserServiceStatusProvider,
    ServiceManager,
    ServiceManagerError,
    detect_runtime_mode,
)
from nextuxlinq.web import create_app
from nextuxlinq.writer_lock import WriterLock, WriterLockHeldError

ServiceFactory = Callable[[], NexTuxLinqService]


class HttpServer(Protocol):
    should_exit: bool

    async def serve(self) -> None: ...


ServerFactory = Callable[[FastAPI, str, int], HttpServer]


class LocalUvicornServer(uvicorn.Server):
    """Uvicorn server that leaves signal ownership to nextuxlinqd."""

    @contextmanager
    def capture_signals(self):
        yield


def validate_host(value: str) -> str:
    """Accept only IP addresses or hostnames resolving entirely to loopback."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(value, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as error:
            raise argparse.ArgumentTypeError("Host could not be resolved.") from error
        if not addresses or any(
            not ipaddress.ip_address(address).is_loopback
            for address in addresses
        ):
            raise argparse.ArgumentTypeError("Host must resolve only to loopback.")
    else:
        if not address.is_loopback:
            raise argparse.ArgumentTypeError("Host must be a loopback address.")
    return value


def validate_port(value: str | int) -> int:
    """Return one valid TCP port."""
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("Port must be an integer.") from error
    if isinstance(value, bool) or not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1 and 65535.")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nextuxlinqd",
        description="Run the local Phanteks Matrix900 NexTuxLinq service core.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="run in the foreground (the current and only mode)",
    )
    parser.add_argument(
        "--host",
        type=validate_host,
        default="127.0.0.1",
        help="loopback address to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=validate_port,
        default=8765,
        help="local HTTP port (default: 8765)",
    )
    return parser


async def wait_for_shutdown(
    service: NexTuxLinqService,
    stop_requested: asyncio.Event,
) -> None:
    """Wait for a signal request or propagate an unexpected renderer failure."""
    stop_task = asyncio.create_task(stop_requested.wait())
    render_task = service.render_task
    if render_task is None:
        stop_task.cancel()
        raise RuntimeError("NexTuxLinq renderer did not start.")
    done, _ = await asyncio.wait(
        (stop_task, render_task),
        return_when=asyncio.FIRST_COMPLETED,
    )
    if render_task in done:
        stop_task.cancel()
        try:
            await stop_task
        except asyncio.CancelledError:
            pass
        await render_task


def _render_task_exception(
    render_task: asyncio.Task[None] | None,
) -> BaseException | None:
    """Return the renderer's own failure when it already finished with one.

    The cancellation path handles a cancellation rather than the renderer
    failure itself, so the original exception has to be read back from the
    finished render task to reach the log.
    """
    if render_task is None or not render_task.done() or render_task.cancelled():
        return None
    return render_task.exception()


def _create_service() -> NexTuxLinqService:
    writer_lock = WriterLock.from_environment(owner="nextuxlinqd")
    worker = DeviceWorker(writer_lock=writer_lock)
    return NexTuxLinqService(config_store=ConfigStore(), device_worker=worker)


def _create_http_server(app: FastAPI, host: str, port: int) -> HttpServer:
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        workers=1,
        log_level="info",
    )
    return LocalUvicornServer(config)


async def run_service(
    *,
    service_factory: ServiceFactory = _create_service,
    stop_requested: asyncio.Event | None = None,
    server_factory: ServerFactory = _create_http_server,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> int:
    """Run the service and local HTTP server until shutdown or failure."""
    host = validate_host(host)
    port = validate_port(port)
    loop = asyncio.get_running_loop()
    install_signal_handlers = stop_requested is None
    if stop_requested is None:
        stop_requested = asyncio.Event()
    if install_signal_handlers:
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, stop_requested.set)

    service = service_factory()
    runtime_mode = detect_runtime_mode()
    server: HttpServer | None = None
    server_task: asyncio.Task[None] | None = None
    stop_task: asyncio.Task[bool] | None = None
    worker_failure_task: asyncio.Task[None] | None = None
    render_task: asyncio.Task[None] | None = None
    logging.info(
        "Starting NexTuxLinq Matrix900 controller at http://%s:%d (%s).",
        host,
        port,
        (
            "systemd user service"
            if runtime_mode == "systemd_user_service"
            else "manual"
        ),
    )
    exit_code = 0
    try:
        await service.start()
        service_snapshot = service.snapshot()
        logging.info("NexTuxLinq DeviceWorker startup successful.")
        get_configuration = getattr(service, "get_configuration", None)
        if callable(get_configuration):
            configuration = get_configuration().configuration
            logging.info(
                "Initial render successful: schema v%d, active profile %s, "
                "update policy %s.",
                configuration.schema_version,
                configuration.active_profile,
                service_snapshot.update_policy,
            )
        else:
            logging.info("Initial render successful.")
        logging.info(
            "Matrix900 selection: %s.",
            "connected" if service_snapshot.device_available else "awaiting device",
        )
        status_provider = None
        try:
            status_provider = CachedUserServiceStatusProvider(
                ServiceManager(),
                runtime_mode=runtime_mode,
            )
        except ServiceManagerError:
            logging.warning("User-service status inspection is unavailable.")
        app = create_app(
            service,
            allowed_hosts={host, "127.0.0.1", "::1", "localhost"},
            configured_port=port,
            service_status_provider=status_provider,
            runtime_mode=runtime_mode,
        )
        server = server_factory(app, host, port)
        server_task = asyncio.create_task(
            server.serve(),
            name="nextuxlinq-http-server",
        )
        stop_task = asyncio.create_task(stop_requested.wait())
        worker_failure_task = asyncio.create_task(
            service.wait_for_worker_failure()
        )
        render_task = service.render_task
        if render_task is None:
            raise RuntimeError("NexTuxLinq renderer did not start.")
        done, _ = await asyncio.wait(
            (stop_task, render_task, server_task, worker_failure_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if server_task in done:
            await server_task
            if stop_task not in done:
                raise RuntimeError("The local HTTP server stopped unexpectedly.")
        if render_task in done:
            await render_task
        if worker_failure_task in done:
            await worker_failure_task
            raise RuntimeError("The Matrix900 device worker stopped.")
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        if service.snapshot().rendering_state == "failed":
            logging.error(
                "NexTuxLinq Matrix900 rendering failed.",
                exc_info=_render_task_exception(render_task),
            )
        else:
            logging.error("NexTuxLinq Matrix900 service failed.")
        exit_code = 1
    except WriterLockHeldError:
        logging.error(
            "%s",
            (
                "Another NexTuxLinq instance already owns the Matrix900 lock."
                if runtime_mode == "systemd_user_service"
                else (
                    "NexTuxLinq is already running as a user service or "
                    "another daemon."
                )
            ),
        )
        exit_code = 1
    except (Exception, SystemExit):
        if service.snapshot().rendering_state == "failed":
            logging.exception("NexTuxLinq Matrix900 rendering failed.")
        else:
            logging.error("NexTuxLinq Matrix900 service failed.")
        exit_code = 1
    finally:
        if server is not None:
            server.should_exit = True
        if server_task is not None and not server_task.done():
            try:
                await server_task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
                exit_code = 1
            except (Exception, SystemExit):
                exit_code = 1
        if stop_task is not None and not stop_task.done():
            stop_task.cancel()
            try:
                await stop_task
            except asyncio.CancelledError:
                pass
        if worker_failure_task is not None and not worker_failure_task.done():
            worker_failure_task.cancel()
            try:
                await worker_failure_task
            except asyncio.CancelledError:
                pass
        if service.owns_resources:
            try:
                await service.stop()
            except TimeoutError:
                logging.error("NexTuxLinq Matrix900 worker shutdown timed out.")
                exit_code = 1
    logging.info("NexTuxLinq Matrix900 service core stopped.")
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the foreground service."""
    arguments = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return asyncio.run(run_service(host=arguments.host, port=arguments.port))


if __name__ == "__main__":
    raise SystemExit(main())
