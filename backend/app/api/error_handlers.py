"""Maps domain errors to one response envelope:
{"error": {"code", "message", "details"}, "request_id"}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AuthenticationError, DomainError, RateLimitedError
from app.core.request_context import get_request_id

logger = logging.getLogger("procurax.errors")


def _envelope(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "details": details},
            "request_id": get_request_id(),
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        response = _envelope(exc.status_code, exc.code, exc.message, exc.details)
        if isinstance(exc, AuthenticationError):
            response.headers["WWW-Authenticate"] = "Bearer"
        if isinstance(exc, RateLimitedError) and isinstance(exc.details, dict):
            response.headers["Retry-After"] = str(exc.details.get("retry_after_seconds", 60))
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
        ]
        return _envelope(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _envelope(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", exc_info=exc)
        return _envelope(500, "internal_error", "An unexpected error occurred")
