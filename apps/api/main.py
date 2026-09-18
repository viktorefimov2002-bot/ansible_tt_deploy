import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from apps.shared.config import Settings, load_settings
from apps.shared.dependencies import connected_dependencies

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        async with connected_dependencies(settings or load_settings()) as dependencies:
            app.state.dependencies = dependencies
            logger.info("api_started")
            try:
                yield
            finally:
                logger.info("api_stopping")
        logger.info("api_stopped")

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz", include_in_schema=False)
    @app.get("/api/healthz", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    @app.get("/api/readyz", include_in_schema=False)
    async def ready():
        checks = await app.state.dependencies.check()
        healthy = all(checks.values())
        return JSONResponse(
            {"status": "ready" if healthy else "not_ready"},
            status_code=200 if healthy else 503,
        )

    return app
