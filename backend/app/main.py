import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.error_handlers import register_exception_handlers
from app.api.router import api_router
from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.modules.procurement.escalations import sla_sweep_loop

API_DESCRIPTION = """
ProcuraX — procurement & spend transformation platform.

Purchase-to-pay workflow with deterministic policy enforcement, multi-tenant isolation
(application scoping + PostgreSQL Row-Level Security) and a complete audit trail.

**Design principle:** AI recommends, rules enforce, humans decide on high-risk actions.
"""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    sweep: asyncio.Task[None] | None = None
    if settings.sla_escalation_interval_seconds > 0 and settings.env != "test":
        sweep = asyncio.create_task(sla_sweep_loop(settings.sla_escalation_interval_seconds))
    yield
    if sweep is not None:
        sweep.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.env != "test":
        configure_logging()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=API_DESCRIPTION,
        lifespan=lifespan,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    # Added last → outermost: every response (incl. CORS preflight) gets a request ID.
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["ops"], include_in_schema=False)
    async def health() -> dict[str, str]:
        """Liveness: the process is up. Does not touch dependencies."""
        return {"status": "ok"}

    @app.get(f"{settings.api_prefix}/meta", tags=["ops"])
    async def meta() -> dict[str, object]:
        """Public deployment facts the SPA adapts to (no secrets, no tenant data)."""
        current = get_settings()
        return {
            "version": app.version,
            "environment": current.env,
            "demo_mode": current.demo_mode,
            "public_demo": current.public_demo,
            "registration_enabled": current.allows_registration,
        }

    @app.get("/ready", tags=["ops"], include_in_schema=False)
    async def ready() -> JSONResponse:
        """Readiness: dependencies reachable. Used by the load balancer / deploy health check."""
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=503, content={"status": "unavailable", "database": type(exc).__name__}
            )
        return JSONResponse({"status": "ready", "database": "ok"})

    return app


app = create_app()
