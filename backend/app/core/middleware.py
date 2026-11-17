"""Pure-ASGI middleware: request IDs, structured access logs, security headers."""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics
from app.core.request_context import set_request_context

logger = logging.getLogger("procurax.access")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_UNMETERED = {"/health", "/ready", "/api/v1/metrics"}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class RequestContextMiddleware:
    """Assigns/propagates X-Request-ID, emits one structured log line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        client = scope.get("client")
        set_request_context(request_id, client[0] if client else None)

        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                for key, value in SECURITY_HEADERS.items():
                    headers.setdefault(key, value)
            await send(message)

        metrics.HTTP_IN_PROGRESS.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            metrics.HTTP_IN_PROGRESS.dec()
            route = scope.get("route")
            elapsed = time.perf_counter() - start
            # The route template keeps label cardinality bounded; unmatched paths share one label.
            template = getattr(route, "path", None) or "unmatched"
            if template not in _UNMETERED:
                method = scope.get("method", "GET")
                metrics.HTTP_REQUESTS.labels(
                    method, template, metrics.status_class(status_holder["status"])
                ).inc()
                metrics.HTTP_LATENCY.labels(method, template).observe(elapsed)
            logger.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "route": getattr(route, "path", None),
                    "status": status_holder["status"],
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
