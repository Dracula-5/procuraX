"""Optional OpenTelemetry tracing (PROCURAX_OTEL_ENABLED, off by default).

Spans for every HTTP request (FastAPI), every SQL statement (SQLAlchemy) and every outgoing HTTP call
(httpx: NTA registry, ECB, OIDC, webhooks), exported over OTLP/HTTP to a collector (Jaeger, Tempo,
Honeycomb, an OpenTelemetry Collector, ...). Request bodies, headers and SQL parameters are not recorded.
The trace id is added to every JSON log line so logs and traces can be joined.
"""

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.core.config import Settings

logger = logging.getLogger("procurax.tracing")
_EXCLUDED = "health,ready,metrics"


def _provider(settings: Settings, service_name: str, exporter: SpanExporter | None) -> TracerProvider:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: PLC0415

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "deployment.environment": settings.env}),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            exporter
            or OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)
    return provider


def _instrument_clients(provider: TracerProvider) -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # noqa: PLC0415
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # noqa: PLC0415

    from app.core.db import get_engine  # noqa: PLC0415

    # The instrumentation declares SQLAlchemy < 2.1 and silently does nothing on 2.1; the engine events it
    # hooks are unchanged in 2.1, so the check is skipped (tests/api/test_observability.py proves the spans).
    SQLAlchemyInstrumentor().instrument(
        engine=get_engine().sync_engine, tracer_provider=provider, skip_dep_check=True
    )
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)


def setup_tracing(app: FastAPI, settings: Settings, exporter: SpanExporter | None = None) -> TracerProvider:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415

    provider = _provider(settings, settings.otel_service_name, exporter)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls=_EXCLUDED)
    _instrument_clients(provider)
    logger.info("tracing_enabled", extra={"endpoint": settings.otel_exporter_otlp_endpoint})
    return provider


def setup_worker_tracing(settings: Settings, exporter: SpanExporter | None = None) -> TracerProvider:
    """The worker has no HTTP server: SQL and outgoing HTTP (SMTP is not traced) only."""
    service = (
        settings.otel_service_name if settings.otel_service_name != "procurax-api" else "procurax-worker"
    )
    provider = _provider(settings, service, exporter)
    _instrument_clients(provider)
    return provider


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
