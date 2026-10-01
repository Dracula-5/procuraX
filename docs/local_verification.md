# Local verification and performance runs

This guide keeps local engineering checks separate from public deployment and business outcomes.

## Read-only API load smoke

`backend/scripts/load_test.py` uses only the Python standard library. The example plan calls the vendor list, spend analytics and supported assistant query. These reads require a user who can read vendors and analytics. Issue a short-lived local token through the normal login flow and supply it in `PROCURAX_LOAD_TOKEN`; do not commit it or paste it into shell history.

PowerShell example:

```powershell
$env:PROCURAX_LOAD_TOKEN = '<short-lived local bearer token>'
cd backend
python scripts/load_test.py --url http://127.0.0.1:8000 --plan scripts/load_plan_read_only.json --concurrency 5 --duration 10
Remove-Item Env:\PROCURAX_LOAD_TOKEN
```

Run a separate capture for each concurrency level (1, 5, 10, 50, 100, 250 and 500) and record the API version, host CPU/memory, database version/size, seed profile, plan, duration, concurrency, warm-up, failures and raw JSON output. A run against `/health` only measures the local HTTP process and is not an API or database capacity result. Do not run high concurrency against a shared or production database.

The harness reports request count, throughput, success ratio, status counts, and p50/p95/p99/mean latency. It does not collect CPU, memory, database wait, query plans or queue depth, so a full P17 report still needs host/database monitoring and representative seeded data. No 50–500 user load result is claimed in this repository.

## Approval SLA escalation

Two equivalent ways to run the idempotent sweep that reassigns an overdue pending step to the next eligible manager, extends its due time by the active tenant SLA and writes an audit event:

- **Inside the API:** set `PROCURAX_SLA_ESCALATION_INTERVAL_SECONDS` (for example `900`). The lifespan task sweeps on that interval.
- **External scheduler:** run `uv run python scripts/run_sla_escalations.py` from `backend/` with Windows Task Scheduler, cron or the `Demo maintenance` GitHub workflow. Before 2026-10-01 this documented command failed with `No module named 'app'`; the script now adds `backend/` to its import path.

Both take the transaction-scoped PostgreSQL advisory lock `pg_try_advisory_xact_lock(7221301)`, so several API replicas plus a cron job never sweep concurrently; a run that finds the lock held reports `skipped`. If the reporting chain has no eligible manager, the step stays pending and the run reports it in `no_target`. A test holds the lock from another connection and asserts the sweep is skipped.

## Local invoice OCR

The P2P page accepts PDF/image uploads up to 18 MiB and calls the local Tesseract adapter; extracted fields are parsed in OCR-tolerant mode and returned for human review, never persisted as an invoice.

Tesseract does not need administrator rights when installed from conda-forge:

```powershell
conda create -y -n procurax-ocr -c conda-forge --override-channels tesseract
# then, in backend/.env
PROCURAX_TESSERACT_PATH=<conda env>\Library\bin\tesseract.exe
PROCURAX_TESSDATA_PATH=<conda env>\share\tessdata
```

PDF input also needs `pdftoppm` (Poppler; MiKTeX ships one) on PATH or in `PROCURAX_PDFTOPPM_PATH`. Tesseract 5.5.3 installed this way ran the end-to-end benchmark in [final_benchmark.md](../reports/final_benchmark.md#invoice-ocr-end-to-end) on synthetic rendered invoices. The parser reads `Label: value` lines only; it does not extract tables or provide confidence values.

## Security configuration

Production settings now fail closed when demo mode or SQL echo is enabled, the public URL is not HTTPS, CORS origins are missing/wildcard/non-HTTPS, or the development JWT key is used. Configure explicit HTTPS origins, a production secret and `PROCURAX_DEMO_MODE=false` before starting a production deployment. Structured log extras redact common secret-bearing field names recursively; do not put secrets in message text.

## Production-shaped container stack

```bash
PROCURAX_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))") \
  docker compose -f docker-compose.stack.yml -p procurax-stack up -d --build
# http://localhost:8088 (set PROCURAX_WEB_PORT to change the host port)
docker compose -f docker-compose.stack.yml -p procurax-stack down -v   # remove, including its database
```

Release order: database healthy, then a one-shot `alembic upgrade head` with the owner role, then the API with production settings (demo mode off, RLS-bound runtime role, in-process SLA sweep), then nginx serving the SPA and proxying `/api`. On 2026-10-01 the stack was built from source and started from an empty database. Through nginx the SPA and a deep link returned 200 with CSP/X-Frame-Options headers, a tenant was registered and `/auth/me` succeeded, demo login returned 404, and five failed logins were followed by 429 with `Retry-After`. Port 8080 was already taken by another local web server on this host, hence the 8088 default.

## Managed-database rehearsal (Neon-like owner)

Managed PostgreSQL services such as Neon give you an owner role that is **not** a superuser, so `FORCE ROW LEVEL SECURITY` applies to it as well. On 2026-10-01 a local database `procurax_neonlike` owned by `neonlike_owner NOSUPERUSER NOBYPASSRLS` was used to run, in order: `alembic upgrade head`, `alembic check`, `seed_demo`, `seed_demo --reset` twice, the SLA script, and the full test suite (163 passed). The rehearsal found that the demo reset and the owner-side test fixtures assumed RLS bypass; both now set `app.rls_bypass` explicitly.

## Remaining verification limits

No cloud account, public host, pilot users, licensed invoice corpus or representative business dataset is present. Model accuracy on real documents, real-user outcomes, production load capacity, cost and ROI remain unmeasured. The login throttle is per API process; behind several replicas, enforce it at the gateway or with a shared store.

## Recorded local-only smoke checks

On the local Windows workspace (Python 3.12.7), a short run of the app liveness endpoint with five workers for two seconds completed 612 requests: all returned HTTP 200, 304.63 req/s, p50 13.05 ms and p95 31.83 ms. It did not call authenticated or database routes and says nothing about API capacity.

