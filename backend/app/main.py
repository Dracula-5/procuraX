import asyncio
import contextlib
import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.error_handlers import register_exception_handlers
from app.api.router import api_router
from app.background import background_loop
from app.core import metrics
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
    tasks: list[asyncio.Task[None]] = []
    if settings.env != "test":
        if settings.sla_escalation_interval_seconds > 0:
            tasks.append(asyncio.create_task(sla_sweep_loop(settings.sla_escalation_interval_seconds)))
        # Free hosting without a separate worker process: run the background cycle in the API instead.
        if settings.notification_worker_interval_seconds > 0:
            tasks.append(asyncio.create_task(background_loop(settings.notification_worker_interval_seconds)))
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
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
        # Explicit origins only (never "*"): the refresh cookie travels on /auth/refresh and /auth/logout
        # when the SPA is on another site; those endpoints also require X-CSRF-Token (ADR-019).
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )
    # Added last → outermost: every response (incl. CORS preflight) gets a request ID.
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    metrics.BUILD_INFO.labels(app.version, os.environ.get("RENDER_GIT_COMMIT", "")[:7] or "local").set(1)
    if settings.otel_enabled:
        from app.core.tracing import setup_tracing  # noqa: PLC0415 - optional, off by default

        setup_tracing(app, settings)

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
            # Deployed revision (Render sets RENDER_GIT_COMMIT) and the browser origins CORS allows,
            # so a deployment can be checked from outside. Neither is secret.
            "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:7] or None,
            "allowed_origins": current.cors_origins,
        }

    @app.get(f"{settings.api_prefix}/metrics", tags=["ops"], include_in_schema=False, response_model=None)
    async def prometheus_metrics(authorization: Annotated[str | None, Header()] = None) -> Response:
        """Prometheus scrape endpoint. Production requires `Authorization: Bearer <PROCURAX_METRICS_TOKEN>`."""
        current = get_settings()
        token = current.metrics_token
        if not current.metrics_enabled or (current.env == "prod" and not token):
            return Response(status_code=404)
        if token and not hmac.compare_digest(authorization or "", f"Bearer {token}"):
            return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
        await metrics.refresh_gauges()
        body, content_type = metrics.render()
        return Response(body, media_type=content_type, headers={"Cache-Control": "no-store"})

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
