"""Foreground entry point for the NexLinq Matrix900 service core."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Callable, Sequence

from nexlinq.config_store import ConfigStore
from nexlinq.device_worker import DeviceWorker
from nexlinq.service import NexLinqService
from nexlinq.writer_lock import WriterLock, WriterLockHeldError

ServiceFactory = Callable[[], NexLinqService]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexlinqd",
        description="Run the local Phanteks Matrix900 NexLinq service core.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="run in the foreground (the current and only mode)",
    )
    return parser


async def wait_for_shutdown(
    service: NexLinqService,
    stop_requested: asyncio.Event,
) -> None:
    """Wait for a signal request or propagate an unexpected renderer failure."""
    stop_task = asyncio.create_task(stop_requested.wait())
    render_task = service.render_task
    if render_task is None:
        stop_task.cancel()
        raise RuntimeError("NexLinq renderer did not start.")
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


def _create_service() -> NexLinqService:
    writer_lock = WriterLock.from_environment(owner="nexlinqd")
    worker = DeviceWorker(writer_lock=writer_lock)
    return NexLinqService(config_store=ConfigStore(), device_worker=worker)


async def run_service(
    *,
    service_factory: ServiceFactory = _create_service,
    stop_requested: asyncio.Event | None = None,
) -> int:
    """Run until a signal or renderer failure requests shutdown."""
    loop = asyncio.get_running_loop()
    install_signal_handlers = stop_requested is None
    if stop_requested is None:
        stop_requested = asyncio.Event()
    if install_signal_handlers:
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, stop_requested.set)

    service = service_factory()
    logging.info("Starting NexLinq Matrix900 service core.")
    exit_code = 0
    try:
        await service.start()
        await wait_for_shutdown(service, stop_requested)
    except WriterLockHeldError as error:
        logging.error("%s", error)
        exit_code = 1
    except Exception:
        logging.error("NexLinq Matrix900 rendering failed.")
        exit_code = 1
    finally:
        if service.owns_resources:
            try:
                await service.stop()
            except TimeoutError:
                logging.error("NexLinq Matrix900 worker shutdown timed out.")
                exit_code = 1
    logging.info("NexLinq Matrix900 service core stopped.")
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the foreground service."""
    build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return asyncio.run(run_service())


if __name__ == "__main__":
    raise SystemExit(main())
