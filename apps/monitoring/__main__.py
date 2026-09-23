"""Independent metrics endpoint; failures never change server state or VPN traffic."""

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import create_async_engine

from apps.monitoring.collector import collect
from apps.persistence.database import database_url
from apps.shared.config import load_database_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    settings = load_database_settings()
    key = os.environ["TTCP_AUTH_ENCRYPTION_KEY"]
    Fernet(key.encode("ascii"))
    app.state.engine = create_async_engine(database_url(settings), pool_size=2, max_overflow=0)
    app.state.key = key
    try:
        yield
    finally:
        await app.state.engine.dispose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    try:
        return await collect(app.state.engine, app.state.key)
    except Exception as exc:
        logger.warning("monitoring_collection_failed", extra={"error_type": type(exc).__name__})
        return PlainTextResponse("collection unavailable\n", status_code=503)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9101, log_level="warning")
