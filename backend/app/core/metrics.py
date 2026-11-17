"""Prometheus metrics: HTTP traffic, database pool, background queues and a few business counters.

Exposed at GET /api/v1/metrics (text exposition format). In production the endpoint answers only with
`Authorization: Bearer <PROCURAX_METRICS_TOKEN>`; without a token configured it is switched off there.
Labels are bounded: the route *template* (never the raw path), the method and the status class.

Queue gauges (outbox backlog, dead deliveries, pending scans, oldest pending event) are read from the
database when Prometheus scrapes, so they are correct whichever process does the work. Counters are per
process; the worker exposes its own on PROCURAX_WORKER_METRICS_PORT.
"""

import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "procurax_http_requests_total", "HTTP requests handled", ["method", "route", "status"], registry=REGISTRY
)
HTTP_LATENCY = Histogram(
    "procurax_http_request_duration_seconds",
    "Time to handle an HTTP request",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)
HTTP_IN_PROGRESS = Gauge(
    "procurax_http_requests_in_progress", "HTTP requests being handled", registry=REGISTRY
)

POLICY_OUTCOMES = Counter(
    "procurax_policy_evaluations_total",
    "Purchase requests evaluated at submission",
    ["outcome"],
    registry=REGISTRY,
)
APPROVAL_DECISIONS = Counter(
    "procurax_approval_decisions_total", "Approval step decisions", ["decision"], registry=REGISTRY
)
AUTH_FAILURES = Counter(
    "procurax_auth_failures_total", "Refused sign-in attempts", ["kind"], registry=REGISTRY
)
NOTIFICATION_DELIVERIES = Counter(
    "procurax_notification_deliveries_total",
    "External notification deliveries",
    ["channel", "result"],
    registry=REGISTRY,
)
DOCUMENT_SCANS = Counter(
    "procurax_document_scans_total", "Invoice file virus scans", ["result"], registry=REGISTRY
)
WORKER_CYCLES = Counter(
    "procurax_worker_cycles_total", "Background work cycles", ["result"], registry=REGISTRY
)
WORKER_LAST_CYCLE = Gauge(
    "procurax_worker_last_cycle_timestamp_seconds",
    "Unix time of the last finished worker cycle",
    registry=REGISTRY,
)

DB_POOL_CHECKED_OUT = Gauge(
    "procurax_db_pool_connections_in_use", "Database connections checked out of the pool", registry=REGISTRY
)
OUTBOX_PENDING = Gauge(
    "procurax_outbox_pending_events", "Outbox events not yet fanned out", registry=REGISTRY
)
OUTBOX_OLDEST_AGE = Gauge(
    "procurax_outbox_oldest_pending_age_seconds",
    "Age of the oldest event not yet fanned out",
    registry=REGISTRY,
)
DELIVERIES_DEAD = Gauge(
    "procurax_notification_deliveries_dead", "Deliveries that exhausted their retries", registry=REGISTRY
)
DELIVERIES_RETRYING = Gauge(
    "procurax_notification_deliveries_retrying", "Deliveries waiting for a retry", registry=REGISTRY
)
SCANS_PENDING = Gauge(
    "procurax_documents_pending_scan", "Invoice files waiting for a virus scan", registry=REGISTRY
)

BUILD_INFO = Gauge("procurax_build_info", "Deployed version", ["version", "commit"], registry=REGISTRY)


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def now() -> float:
    return time.time()


_QUEUES_SQL = """
SELECT
  (SELECT count(*) FROM notification_outbox WHERE status = 'pending') AS outbox_pending,
  (SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0)
     FROM notification_outbox WHERE status = 'pending') AS outbox_oldest,
  (SELECT count(*) FROM notification_deliveries WHERE status = 'dead') AS deliveries_dead,
  (SELECT count(*) FROM notification_deliveries WHERE status = 'pending' AND attempts > 0) AS deliveries_retrying,
  (SELECT count(*) FROM invoice_documents WHERE scan_status = 'pending') AS scans_pending
"""


async def refresh_gauges() -> None:
    """Read pool and queue state just before a scrape (cheap aggregate queries, no tenant data)."""
    from sqlalchemy import text  # noqa: PLC0415

    from app.core.db import get_engine, system_session  # noqa: PLC0415

    pool = get_engine().pool
    checked_out = getattr(pool, "checkedout", None)
    if callable(checked_out):
        DB_POOL_CHECKED_OUT.set(checked_out())
    async with system_session() as session:
        row = (await session.execute(text(_QUEUES_SQL))).one()
    OUTBOX_PENDING.set(row.outbox_pending)
    OUTBOX_OLDEST_AGE.set(float(row.outbox_oldest or 0))
    DELIVERIES_DEAD.set(row.deliveries_dead)
    DELIVERIES_RETRYING.set(row.deliveries_retrying)
    SCANS_PENDING.set(row.scans_pending)


def status_class(status: int) -> str:
    return f"{status // 100}xx"
