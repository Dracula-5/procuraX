# Operations runbook

What to look at and what to do when something is wrong. Every Prometheus alert in
[`infra/observability/alerts.yml`](../infra/observability/alerts.yml) links to a section here. Commands assume
the repository root; database work uses the **owner** connection (`PROCURAX_DATABASE_ADMIN_URL`), never the
runtime role.

## Where to look

| Signal | Where |
|---|---|
| Metrics | `GET /api/v1/metrics` (Prometheus format; production needs `Authorization: Bearer $PROCURAX_METRICS_TOKEN`, and is off without a token). Worker: `:$PROCURAX_WORKER_METRICS_PORT/metrics` |
| Dashboard | Grafana "ProcuraX overview" (local: `docker compose -f docker-compose.stack.yml --profile observability up -d`, then http://localhost:3001) |
| Traces | Off unless `PROCURAX_OTEL_ENABLED=true`; OTLP/HTTP to `PROCURAX_OTEL_EXPORTER_OTLP_ENDPOINT` (local Jaeger: http://localhost:16686) |
| Logs | One JSON object per line with `request_id` (also the `X-Request-ID` response header) and, with tracing on, `trace_id` |
| Health | `/health` (process up), `/ready` (database reachable), `/api/v1/meta` (deployed commit, CORS origins) |
| Audit | `GET /api/v1/audit-logs` (admins): who did what, including sign-ins, MFA changes and exports |

Key metrics: `procurax_http_requests_total{route,status}`, `procurax_http_request_duration_seconds`,
`procurax_outbox_pending_events`, `procurax_outbox_oldest_pending_age_seconds`,
`procurax_notification_deliveries_dead`, `procurax_documents_pending_scan`,
`procurax_db_pool_connections_in_use`, `procurax_auth_failures_total{kind}`,
`procurax_policy_evaluations_total{outcome}`, `procurax_worker_last_cycle_timestamp_seconds`.

## API down

`ProcuraXApiDown` — Prometheus cannot scrape the API.

1. `curl -s https://<api>/health` — no answer: the process is down or (free tier) asleep. On Render, check
   the service's Events and Logs; a crash loop at start usually prints `Production settings incomplete:`
   with every missing variable.
2. `/health` answers but `/ready` is 503: the database is unreachable. Check the provider's status page and
   the connection limit (see [Database connections](#database-connections)).
3. After a deploy: roll back to the previous deploy in the platform, then investigate.

## 5xx errors

`ProcuraXHighErrorRate` — more than 5 % of requests fail with 5xx for 10 minutes.

1. In the dashboard, "Requests by status" and "Slowest routes" show which route.
2. Search the logs for `"status": 500` and take a `request_id`; with tracing on, open the trace from the
   log line's `trace_id`.
3. A database error after a release usually means migrations did not run: `uv run alembic current` must
   show the head revision.
4. If one route fails because an outside service is down (NTA, ECB, SMTP, OIDC provider), it should
   degrade, not 500 — record it as a bug.

## Slow API

`ProcuraXSlowRequests` — p95 above 1.5 s for 15 minutes.

1. "Slowest routes (p95)" names the route; a trace shows which SQL statement or outside call is slow.
2. Free-tier cold starts show up as single slow requests after idle periods; sustained slowness does not.
3. Check `procurax_db_pool_connections_in_use` (pool exhaustion queues requests) and the database's own
   CPU and connection graphs.

## Worker stalled

`ProcuraXOutboxStalled` — the oldest pending notification event is more than 10 minutes old.

1. Is a worker running? Either the in-process loop (`PROCURAX_NOTIFICATION_WORKER_INTERVAL_SECONDS>0`) or
   `python -m app.worker`. `procurax_worker_last_cycle_timestamp_seconds` shows the last finished cycle.
2. Run one cycle by hand and read its counts: `uv run python -m app.worker --once`.
3. On the free Render plan the API sleeps; the hourly `demo-maintenance` workflow runs a cycle. Wake the
   API or run the workflow manually.
4. Workers can run in parallel (rows are claimed with `FOR UPDATE SKIP LOCKED`), so starting another one
   is safe.

## Dead deliveries

`ProcuraXDeadDeliveries` — e-mail, Slack or Teams deliveries gave up after their retries.

1. `SELECT channel, last_error, count(*) FROM notification_deliveries WHERE status = 'dead' GROUP BY 1, 2;`
2. SMTP authentication or Slack/Teams 4xx answers mean configuration (credentials, a revoked webhook).
   Fix it, then retry from the organisation's notification settings (admins) or
   `POST /api/v1/notification-deliveries/{id}/retry`.
3. In-app notifications are not affected; nothing is lost.

## Scanner down

`ProcuraXScansBacklog` — invoice files are waiting for a virus scan. Payments on those invoices are blocked
by design.

1. Is clamd reachable from the worker (`PROCURAX_CLAMAV_HOST`, port 3310)? `docker logs <clamav>`; the
   first start downloads signatures and takes minutes.
2. Pending files retry with backoff on their own once clamd answers.
3. Do **not** set `PROCURAX_ALLOW_UNSCANNED_DOCUMENTS=true` to clear the backlog; that only concerns
   deployments without scanning.

## Database connections

`ProcuraXDbPoolExhausted` — the API is using (almost) all pooled connections.

1. Free plans allow few connections; `PROCURAX_DB_POOL_SIZE` + `PROCURAX_DB_MAX_OVERFLOW` per process,
   times the number of processes (API replicas, workers, migrations), must stay under the provider's limit.
2. `SELECT state, count(*) FROM pg_stat_activity GROUP BY 1;` — many `idle in transaction` sessions point
   at a code path that does not close its session; report it with the route.

## Sign-in attack

`ProcuraXLoginAttack` — many refused sign-ins in 15 minutes.

1. The throttle already refuses further attempts per address, per account and per address + account
   (`auth_throttle`). Nothing to do for a burst against unknown accounts.
2. If one account is targeted, ask the person to turn on two-factor sign-in. An administrator can revoke
   their sessions by deactivating and reactivating the account.
3. Persistent attacks from a few networks: block them at the platform's edge (Netlify / Render / WAF).

## Backups

- **Provider:** Neon keeps point-in-time history for its retention window (check the plan). That covers
  "undo the last hour"; it is not an independent copy.
- **Independent copy:** `.github/workflows/backup.yml` dumps nightly with `pg_dump --format=custom`,
  restores the dump into a throw-away PostgreSQL to prove it, encrypts it with `gpg --symmetric`
  (AES-256) and keeps it 7 days as a workflow artifact. Enable it with the repository variable
  `PROCURAX_BACKUPS_ENABLED=true` and the secrets `PROCURAX_BACKUP_DATABASE_URL` and
  `PROCURAX_BACKUP_PASSPHRASE`. Store the passphrase outside GitHub too.
- **Object storage (invoice and contract files):** enable bucket versioning (and object lock where
  required). The database stores each file's SHA-256, so a restored database can verify the files.
- **Drill:** `uv run python -m app.scripts.restore_drill` dumps a database, restores it into a scratch
  database and checks the Alembic revision, row counts per table, forced RLS with the tenant policy on every
  tenant table, that the runtime role sees nothing without a tenant and exactly one tenant with one, and
  that the audit log is still append-only. CI runs it on every push; the last local run is in
  [`reports/restore_drill.json`](../reports/restore_drill.json).

## Restore

Restoring production replaces live data: agree the point in time with the business owner first, stop
writes (put the web app in maintenance or scale the API to zero), and keep the current database until the
restored one is verified.

1. Get the dump: download the artifact, then `gpg --decrypt procurax-<stamp>.dump.gpg > procurax.dump` and
   compare `sha256sum procurax.dump` with the `.sha256` file.
2. Create an empty database (or a Neon branch) and the runtime role if missing:
   `CREATE ROLE procurax_app LOGIN PASSWORD '…';`
3. `pg_restore --no-owner --exit-on-error --single-transaction --dbname="$TARGET_OWNER_URL" procurax.dump`
4. Verify against it: `PROCURAX_DATABASE_ADMIN_URL=$TARGET_OWNER_URL uv run alembic current` (head),
   then run the drill with `--source-url $TARGET_OWNER_URL` to repeat the RLS and role checks.
5. Point `PROCURAX_DATABASE_URL` (runtime role) and `PROCURAX_DATABASE_ADMIN_URL` at it, redeploy, check
   `/ready`, sign in, open a request and the audit log.
6. Files: if objects were lost, restore the bucket from its versions; the scan worker re-hashes files it
   scans.

## Deploy and roll back

- Deploy: push to `main` → CI → `deploy.yml` runs migrations, then Render redeploys; Netlify rebuilds.
- Roll back the code: redeploy the previous commit on Render / Netlify. Migrations are reversible
  (`uv run alembic downgrade <revision>`), and every downgrade is tested on a populated database in CI, but
  roll the schema back only when the new code is already off.

## Secrets and keys

| Secret | Rotate how | Effect |
|---|---|---|
| `PROCURAX_JWT_SECRET` | Replace and redeploy | Every access token and CSRF token becomes invalid; users sign in again |
| `PROCURAX_DATA_ENCRYPTION_KEY` | Put the new key first, keep the old after a comma, redeploy | Webhook URLs and TOTP secrets stay readable; drop the old key only after re-saving them |
| `PROCURAX_METRICS_TOKEN` | Replace in the API and in Prometheus | Scrapes fail until both match |
| `PROCURAX_OIDC_CLIENT_SECRET` | Rotate at the identity provider, then here | SSO fails in between |
| SMTP / S3 / NTA credentials | Rotate at the provider, then here | That integration fails in between |

## Contacts and ownership

Fill in before a pilot: who is on call, who can approve a restore, the hosting accounts (Render, Neon,
Netlify, object storage) and the identity provider's administrators.
