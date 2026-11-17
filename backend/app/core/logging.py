"""JSON structured logging (one object per line), so logs are machine-queryable."""

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.request_context import get_request_id


def _trace_id() -> str | None:
    try:
        from opentelemetry import trace  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return None
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
_SENSITIVE_MARKERS = ("password", "secret", "token", "authorization", "cookie", "apikey", "credential")


def _safe_extra(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(
                marker in str(key).casefold().replace("_", "").replace("-", "")
                for marker in _SENSITIVE_MARKERS
            )
            else _safe_extra(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_extra(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or get_request_id()
        if request_id:
            payload["request_id"] = request_id
        trace_id = _trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = _safe_extra(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn's own access log duplicates ours; keep its error log.
    logging.getLogger("uvicorn.access").disabled = True
