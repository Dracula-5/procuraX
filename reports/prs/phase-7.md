# Phase 7: metrics, tracing, dashboards, backups and restore drill, runbook

Branch: `gaps/phase-7-observability` (stacked on `gaps/phase-6-security`)

## What changed

### Metrics (ADR-022)
- `prometheus-client` 0.26 (via `uv add`). `app/core/metrics.py`: request count and latency histogram per
  route template / method / status class, requests in flight, DB pool use, queue gauges read from the
  database at scrape time (outbox backlog, oldest pending event age, retrying and dead deliveries, files
  waiting for a scan), counters for policy outcomes, approval decisions, refused sign-ins (by purpose),
  notification deliveries, document scans, worker cycles and the last cycle time, build info.
- `GET /api/v1/metrics`: `Authorization: Bearer $PROCURAX_METRICS_TOKEN` when a token is set; in production
  off (404) without one; `PROCURAX_METRICS_ENABLED` switches it off entirely. Scrapes, `/health` and `/ready`
  are not counted as traffic. `render.yaml` now generates the token.
- Worker: `PROCURAX_WORKER_METRICS_PORT` serves the worker's own registry; each cycle refreshes the queue
  gauges too.

### Tracing
- OpenTelemetry SDK + OTLP/HTTP exporter + FastAPI, SQLAlchemy and httpx instrumentations, behind
  `PROCURAX_OTEL_ENABLED` (off). API and worker (`procurax-worker`) services; sampling ratio; health and
  metrics endpoints excluded; no bodies, headers or SQL parameters. `trace_id` added to JSON log lines.
- Found and handled: the SQLAlchemy instrumentation declares `sqlalchemy < 2.1` and silently did nothing on
  2.1.1; its dependency check is skipped (the engine events are unchanged) and a test proves SQL spans.

### Dashboards and alerts
- `infra/observability/`: Prometheus config (API + worker targets, bearer token), eight alert rules each
  linked to a runbook section, Grafana provisioning (datasource + "ProcuraX overview", 12 panels).
- `docker-compose.stack.yml`: `observability` profile (Prometheus 3.7.3, Grafana 12.2.1, Jaeger 2.11.0);
  the stack's API and worker get the metrics token and the OTLP endpoint.
- `backend/scripts/observability_check.py`: drives traffic through the stack and checks Prometheus,
  Grafana and Jaeger.

### Backups and restore (ADR-023)
- `app/scripts/restore_drill.py`: `pg_dump --format=custom` → SHA-256 → scratch database →
  `pg_restore --single-transaction --exit-on-error` → checks (revision, row counts per table, forced RLS +
  tenant policy on every tenant table, runtime role fails closed and sees exactly one tenant, audit log
  still append-only) → JSON report; tools can run through `docker exec` / `docker run`.
- CI runs the drill after the migration round trip (demo tenant seeded first).
- `.github/workflows/backup.yml`: nightly dump, restore check into a throw-away PostgreSQL, gpg AES-256
  encryption, 7-day artifact; off until `PROCURAX_BACKUPS_ENABLED=true` and the two secrets exist.

### Docs
- `docs/runbook.md`: where to look, one section per alert, backups, restore procedure, deploy and roll
  back, secret rotation, ownership to fill in. ADR-022/023, system design §12–13, deployment, README,
  interview guide, `.env.example`, roadmap.

## How it was tested
- `tests/api/test_observability.py` (4): route templates as labels (no raw ids), latency histogram, policy
  and refused-sign-in counters, outbox gauge from the database, build info; token required when set,
  production default off, disable flag; worker cycle counters; tracing with an in-memory exporter (server
  span with `http.route`, SQL spans without bound parameters, health check excluded, `trace_id` in logs).
- **Real restore drill** on the local PostgreSQL 16 with the demo tenant seeded: 39 tables, 299 rows,
  dump 0.51 s, restore 2.46 s, verification 2.55 s; all six checks passed
  ([report](../restore_drill.json)).
- **Real stack check** (`docker compose … --profile observability up --build`, then
  `scripts/observability_check.py`): both scrape targets up, HTTP, policy, sign-in and worker series
  present, 8 alert rules loaded, Grafana dashboard provisioned and answering a query, 20 API traces with
  SQL spans and 5 worker traces in Jaeger, metrics refused without the token through nginx
  ([report](../observability_check.json)).

## Still open
- No hosted metrics/tracing backend for the live demo (needs an account, e.g. Grafana Cloud); the token is
  generated on Render so one can be connected.
- The nightly backup workflow has not run: it needs your database owner URL and a passphrase as GitHub
  secrets.
- Alert thresholds are starting points, not SLOs agreed with a customer; no paging integration.
- AI metrics (latency, tokens, confidence) come with Phase 8.
