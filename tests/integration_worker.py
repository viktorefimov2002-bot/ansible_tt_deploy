"""Disposable worker process: only the internal no-op handler can execute."""

import argparse
import asyncio
import os
import re
import signal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from apps.jobs.redis import RedisTransport
from apps.jobs.service import JobService
from apps.jobs.worker import HANDLERS, Handler, Worker
from apps.persistence.database import database_url
from apps.shared.config import load_settings
from tests.disposable import validate_ci_environment


async def serve(schema: str, prefix: str, pause: bool):
    validate_ci_environment(os.environ)
    if not re.fullmatch(r"ttcp_test_[a-f0-9]{32}", schema):
        raise ValueError("Only disposable schemas are allowed")
    if not re.fullmatch(r"ttcp:test:[a-f0-9]{32}", prefix):
        raise ValueError("Only disposable Redis namespaces are allowed")
    settings = load_settings()
    engine = create_async_engine(
        database_url(settings),
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema, "statement_timeout": "10000"}},
    )
    redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    transport = RedisTransport(redis, prefix)
    jobs = JobService(engine, transport, transport)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(stop.set))

    async def paused(context):
        await context.checkpoint(50)
        await asyncio.Event().wait()

    handlers = {"internal.noop": Handler(paused, True, True)} if pause else HANDLERS
    try:
        await Worker(jobs, handlers, grace=0.1).run(stop)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema")
    parser.add_argument("prefix")
    parser.add_argument("--pause", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.schema, arguments.prefix, arguments.pause))
