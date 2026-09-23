import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from apps.api.auth import router
from apps.api.auth_service import AuthService
from apps.api.jobs import router as jobs_router
from apps.api.servers import router as servers_router
from apps.api.vpn import router as vpn_router
from apps.jobs.redis import RedisTransport
from apps.jobs.service import JobService
from apps.servers.service import ServerError, ServerService
from apps.shared.config import Settings, load_settings
from apps.shared.dependencies import connected_dependencies
from apps.vpn.service import VpnError, VpnService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        configured = settings or load_settings()
        async with connected_dependencies(configured) as dependencies:
            app.state.dependencies = dependencies
            transport = RedisTransport(dependencies.redis)
            app.state.jobs = JobService(dependencies.engine, transport, transport)
            app.state.servers = ServerService(
                dependencies.engine,
                configured.auth_encryption_key.get_secret_value()
                if configured.auth_encryption_key
                else None,
            )
            app.state.vpn = VpnService(
                dependencies.engine,
                configured.auth_encryption_key.get_secret_value()
                if configured.auth_encryption_key
                else None,
            )
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
    app.include_router(jobs_router)
    app.include_router(servers_router)
    app.include_router(vpn_router)

    @app.exception_handler(ServerError)
    async def server_error(request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(VpnError)
    async def vpn_error(request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith(
            ("/api/auth", "/api/jobs", "/api/servers", "/api/vpn-users")
        ):
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
