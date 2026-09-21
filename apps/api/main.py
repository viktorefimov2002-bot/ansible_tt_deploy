import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from apps.api.auth import router
from apps.api.auth_service import AuthService
from apps.shared.config import Settings, load_settings
from apps.shared.dependencies import connected_dependencies

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        configured = settings or load_settings()
        async with connected_dependencies(configured) as dependencies:
            app.state.dependencies = dependencies
            app.state.auth = (
                AuthService(
                    dependencies.engine,
                    configured.auth_encryption_key.get_secret_value(),
                    configured.auth_session_seconds,
                )
                if configured.auth_encryption_key
                else None
            )
            logger.info("api_started")
            try:
                yield
            finally:
                logger.info("api_stopping")
        logger.info("api_stopped")

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/auth"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # FastAPI's default errors echo submitted values, including passwords/OTP.
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(request, exc):
        logger.warning("database_request_failed", extra={"error_type": type(exc).__name__})
        return JSONResponse({"detail": "Service unavailable"}, status_code=503)

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
