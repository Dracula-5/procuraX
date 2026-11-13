"""Prometheus metrics endpoint, bounded labels, queue gauges, business counters and optional tracing."""

import json
import logging
import re

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.background import run_cycle
from app.core import metrics
from app.core.config import get_settings
from app.core.logging import JsonFormatter
from tests.helpers import API, build_org, create_pr, submit

METRICS = f"{API}/metrics"


def sample(text: str, name: str, **labels: str) -> float | None:
    """The value of one sample in Prometheus text format (labels must match exactly those given)."""
    for line in text.splitlines():
        if not line.startswith(name + "{") and not line.startswith(name + " "):
            continue
        found = dict(re.findall(r'(\w+)="([^"]*)"', line.split(" ")[0]))
        if all(found.get(k) == v for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    return None


async def test_metrics_use_route_templates_and_count_business_events(client: AsyncClient) -> None:
    org = await build_org(client)
    before = (await client.get(METRICS)).text
    submitted_before = sample(before, "procurax_policy_evaluations_total", outcome="requires_approval") or 0
    pr = await submit(client, org, await create_pr(client, org, amount="200000"))
    await client.get(f"{API}/purchase-requests/{pr['id']}", headers=org["employee"].headers)
    await client.post(
        f"{API}/auth/login", json={"email": org["employee"].email, "password": "wrong-password-9"}
    )

    response = await client.get(METRICS)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert pr["id"] not in text  # raw ids never become label values
    assert sample(
        text,
        "procurax_http_requests_total",
        route="/purchase-requests/{pr_id}",
        status="2xx",
        method="GET",
    )
    assert sample(
        text,
        "procurax_http_request_duration_seconds_count",
        route="/purchase-requests/{pr_id}",
        method="GET",
    )
    assert (
        sample(text, "procurax_policy_evaluations_total", outcome="requires_approval") == submitted_before + 1
    )
    assert (sample(text, "procurax_auth_failures_total", kind="login") or 0) >= 1
    # The submission queued an approval notification: the backlog gauge reads the database.
    assert (sample(text, "procurax_outbox_pending_events") or 0) >= 1
    assert sample(text, "procurax_outbox_oldest_pending_age_seconds") is not None
    assert sample(text, "procurax_db_pool_connections_in_use") is not None
    assert sample(text, "procurax_build_info", version="0.1.0") == 1
    assert "/api/v1/metrics" not in text  # scrapes are not counted as traffic


async def test_metrics_token_and_production_default(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "metrics_token", "scrape-token-for-tests-0123456789")
    assert (await client.get(METRICS)).status_code == 401
    assert (await client.get(METRICS, headers={"Authorization": "Bearer wrong"})).status_code == 401
    ok = await client.get(METRICS, headers={"Authorization": "Bearer scrape-token-for-tests-0123456789"})
    assert ok.status_code == 200
    monkeypatch.setattr(settings, "metrics_token", None)
    monkeypatch.setattr(settings, "env", "prod")
    assert (await client.get(METRICS)).status_code == 404  # no token configured: off in production
    monkeypatch.setattr(settings, "env", "test")
    monkeypatch.setattr(settings, "metrics_enabled", False)
    assert (await client.get(METRICS)).status_code == 404


async def test_worker_cycle_metrics() -> None:
    before = metrics.WORKER_CYCLES.labels("ok")._value.get()
    await run_cycle()
    assert metrics.WORKER_CYCLES.labels("ok")._value.get() == before + 1
    assert metrics.WORKER_LAST_CYCLE._value.get() > 0


async def test_tracing_records_request_and_sql_spans_and_tags_logs() -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from app.core.tracing import setup_tracing
    from app.main import create_app

    traced = create_app()
    exporter = InMemorySpanExporter()
    provider = setup_tracing(traced, get_settings(), exporter=exporter)
    try:
        async with AsyncClient(transport=ASGITransport(app=traced), base_url="http://test") as c:
            assert (await c.get("/ready")).status_code == 200  # excluded from tracing
            refused = await c.post(
                f"{API}/auth/login", json={"email": "nobody@example.com", "password": "x" * 12}
            )
            assert refused.status_code == 401
        provider.force_flush()
        spans = exporter.get_finished_spans()
        assert any(s.attributes.get("http.route") == "/api/v1/auth/login" for s in spans), [
            s.name for s in spans
        ]
        sql = [s for s in spans if s.attributes.get("db.system") == "postgresql"]
        assert sql and all("x" * 12 not in str(s.attributes) for s in sql)  # no bound parameters recorded
        assert not any("/ready" in s.name for s in spans)
        with provider.get_tracer("test").start_as_current_span("unit"):
            record = logging.LogRecord("procurax.test", logging.INFO, __file__, 1, "hello", (), None)
            line = json.loads(JsonFormatter().format(record))
        assert re.fullmatch(r"[0-9a-f]{32}", line["trace_id"])
    finally:
        FastAPIInstrumentor.uninstrument_app(traced)
        SQLAlchemyInstrumentor().uninstrument()
        HTTPXClientInstrumentor().uninstrument()
        provider.shutdown()
