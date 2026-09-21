"""Durable job worker with connectivity monitoring and graceful shutdown."""

import asyncio
import logging
import signal

from apps.jobs.redis import RedisTransport
from apps.jobs.service import JobService
from apps.jobs.worker import Worker
from apps.shared.config import ConfigurationError, Settings, load_settings
from apps.shared.dependencies import DependencyUnavailable, connected_dependencies
from apps.shared.logging import configure_logging

logger = logging.getLogger(__name__)


async def run(settings: Settings, stop: asyncio.Event):
    async with connected_dependencies(settings) as dependencies:
        logger.info("worker_started")
        transport = RedisTransport(dependencies.redis)
        worker = Worker(JobService(dependencies.engine, transport, transport))
        consumer = asyncio.create_task(worker.run(stop))
        previous = True
        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_check_interval)
                except TimeoutError:
                    if consumer.done():
                        await consumer
                    healthy = all((await dependencies.check()).values())
                    if healthy != previous:
                        logger.info(
                            "worker_readiness_changed",
                            extra={"state": "ready" if healthy else "not_ready"},
                        )
                        previous = healthy
        finally:
            logger.info("worker_stopping")
            stop.set()
            await consumer
    logger.info("worker_stopped")


async def serve(settings: Settings):
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    previous_handlers = {}
    # signal.signal also supports local Windows development; callbacks wake the loop.
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await run(settings, stop)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def main():
    configure_logging("worker")
    try:
        settings = load_settings()
        configure_logging("worker", settings.log_level)
        asyncio.run(serve(settings))
    except (ConfigurationError, DependencyUnavailable) as exc:
        logger.error(str(exc))
        return 1
    except Exception as exc:
        logger.error("worker_failed", extra={"error_type": type(exc).__name__})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
