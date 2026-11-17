# Phase 2: notifications, outbox and worker

Branch: `gaps/phase-2-notifications` (stacked on `gaps/phase-1-screens-i18n`)

## What changed

### Outbox written with the change
- Migration `0014_notifications_outbox`: `notification_outbox`, `notification_deliveries`,
  `notifications`, `notification_preferences`, `notification_channels`. All tenant tables with
  `ENABLE` + `FORCE` RLS and 0001's `tenant_isolation` predicate (including the explicit `app.rls_bypass`,
  which the cross-tenant worker uses). The runtime role gets SELECT/INSERT/UPDATE; DELETE only on
  `notification_channels`.
- `notifications.outbox.emit` adds a row to the caller's session, so the event commits or rolls back
  with the change. Emitted for: approval routed (submit, each activated step, the finance step added by
  the final budget re-check), escalated (SLA sweep), delegated, request rejected, request approved,
  PO sent, invoice mismatch, payment awaiting approval. Recipients are named users and/or roles.

### Worker (PostgreSQL only — ADR-013)
- `python -m app.worker` (`--once`, `--interval`), or `PROCURAX_NOTIFICATION_WORKER_INTERVAL_SECONDS` to
  run it inside the API, or the hourly GitHub Actions cron on a sleeping free instance.
- Stage 1 fans pending events out (claimed `FOR UPDATE SKIP LOCKED`, one savepoint per event) into in-app
  rows and external deliveries keyed `event:channel:recipient` with a unique constraint, so replays are
  no-ops. The actor is never notified; muted events and e-mail opt-out are respected.
- Stage 2 delivers (claimed the same way). Failures back off exponentially (30 s doubling, cap 1 h,
  deterministic ±20 % jitter) and are dead-lettered after `PROCURAX_NOTIFICATION_MAX_ATTEMPTS` (6).
  At-least-once; the delivery key is the e-mail Message-ID and the webhook `Idempotency-Key`.
- Pure rules in `app/domain/notifications.py` (plan, backoff, dead-letter, message text in en/ja,
  webhook URL validation).

### Channels
- **In-app:** bell with unread count in the header (polls every 30 s) and a Notifications page. The SPA
  renders text from event type + payload, so notifications follow the UI language (ADR-012).
- **E-mail:** SMTP (stdlib `smtplib` in a thread), in the recipient's `users.locale`. Mailpit added to
  `docker-compose.yml` and the production-shaped stack (which also gets a `worker` service). Off unless
  `PROCURAX_EMAIL_ENABLED`.
- **Slack / Teams:** incoming webhooks per tenant, configured by the org admin (events, enabled). URL must
  be https on an allow-listed Slack/Microsoft host, re-checked before every post (SSRF), stored encrypted
  (Fernet via HKDF from `PROCURAX_DATA_ENCRYPTION_KEY`, rotation supported), never returned. Teams gets
  an Adaptive Card. Off unless `PROCURAX_WEBHOOKS_ENABLED`; production refuses the dev key only when
  webhooks are on, so the live demo keeps starting.
- **Preferences:** per user, e-mail on/off and muted event types.
- **Dead letters:** org admins list failed deliveries and retry them (audited).

### Found and fixed
- **Audit rows with NULL entity ids.** UUID primary keys were generated at flush, so `invoice.submitted`,
  `purchase_order.created` and `procurement_knowledge.version_added` audit rows (written before the
  flush) had `entity_id = NULL` — every one in the test database. IDs are now assigned at construction.

### Deployment and docs
`render.yaml` (in-process worker on the free web service; paid worker block commented; encryption key),
`demo-maintenance.yml` (hourly `--once`), `.env.example`, deployment and local-verification guides,
system design (diagram, tables, integrations, security, reliability, technology table), ADR-013, README,
roadmap, product spec, interview guide.

## How it was tested
- Unit (`tests/unit/test_notifications_domain.py`, 21 tests): delivery planning (actor excluded, muted,
  opt-out, no address, deployment switches, tenant subscriptions, dedupe), backoff bounds and
  determinism, dead-letter threshold, en/ja rendering with missing facts, webhook URL allow-list
  (SSRF cases: http, credentials, port, suffix tricks, metadata IP), encryption round trip and rotation,
  production key guard.
- Integration (`tests/api/test_notifications.py`, 13 tests): outbox row written with the submission and
  none for a blocked one; emitted row rolls back with its transaction; in-app delivery to the next
  approver only; role-pool fan-out; two concurrent workers plus a full replay create no duplicates;
  real SMTP (aiosmtpd) e-mail in Japanese; backoff → dead letter → admin retry → sent; preferences;
  webhook validation, encryption at rest, Slack text and Teams card; HTTP transport headers against a
  local server; every workflow event; RLS on all five tables as the runtime role (tenant scoping,
  fail-closed, cross-tenant insert rejected); runtime role cannot delete history.
- Frontend (4 tests): bell count and wording in en/ja from stored facts; mark read / read all;
  preferences save muted events.
- **End to end with real processes:** API under uvicorn, demo seed, real `python -m app.worker --once`
  as a separate process, Mailpit as the SMTP server (see `docs/local_verification.md`).

## Still open
- No real mail provider, Slack workspace or Teams tenant was used (needs your credentials/webhooks).
- No retention/cleanup of old outbox and delivery rows; invitation e-mails are still not sent.
- At-least-once delivery: a crash between a receiver accepting a message and the commit resends it.
