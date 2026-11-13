# Gap closure report

One row per gap from the gap-closure brief. Status: ✅ done · 🟡 partial · ⬜ not done (with the
reason). "Not started" means the phase has not been reached yet. This file is updated at the end of
every phase; the roadmap's "Gap-closure programme" table summarises it.

Verification environment for everything marked ✅ unless stated otherwise: Windows 11 workstation,
Docker Desktop, `pgvector/pgvector:pg16`, Python 3.12 via uv, Node 22. Nothing here was run against
the live deployment.

## Phase 0: docs match the code (branch `gaps/phase-0-docs`, notes: [prs/phase-0.md](prs/phase-0.md))

| Gap | Status | Verified by | Remaining |
|---|---|---|---|
| Permission count (P2 report said 24; code has 23) | ✅ | `check_doc_claims` fails on the original text, passes after the fix | — |
| Render region (render.yaml Singapore vs measured Oregon) | ✅ | Owner confirmed Oregon; `render.yaml`, `deployment.md` and the smoke report agree | Moving to Singapore needs a new Render service (region is fixed at creation) |
| pgvector wording (image only until Phase 8) | ✅ | Repository search: no `CREATE EXTENSION`, no vector column; four statements in `system_design.md` corrected | — |
| CI check: README/roadmap counts vs `rbac.py` and the rule catalogue | ✅ | `tests/unit/test_doc_claims.py` (6 tests) + CI step | — |
| *Found:* migration 0007 downgrade not data-safe | ✅ | Downgrade to base on a populated test DB (16 invoices, 56 requests), upgrade, `alembic check` | — |
| *Found:* `pip-audit` / `npm audit` findings on the baseline | ✅ | `pip-audit`: none; `npm audit`: 0 | — |

## Phase 1: missing screens and Japanese UI (branch `gaps/phase-1-screens-i18n`, notes: [prs/phase-1.md](prs/phase-1.md))

| Gap | Status | Verified by | Remaining |
|---|---|---|---|
| Delegation screen (list, create, revoke; same eligibility rules) | ✅ | API tests: candidates and create share one eligibility query; reason codes; tenant scoping. Vitest: candidate list, delegate, revoke only where `can_revoke` | Per step, not a date-range out-of-office rule |
| Vendor bank account screen with inline Zengin validation | ✅ | TS mirror and `domain/zengin.py` both pass the shared `zengin.cases.json` (22 cases); Vitest form tests (inline problems, preview, save, server refusal next to the field) | — |
| Payer payment-settings screen | ✅ | API tests: admin and finance read (masked), only admin writes; consignor name/code validated at save (was only at export) | — |
| Japanese UI: i18n (en/ja), all UI strings | ✅ | Typed keys (`tsc -b` fails on a missing key); Vitest: identical keys and placeholders, no empty or untranslated strings, rule labels equal the generated rule catalogue | Native-speaker review of the wording; server-generated free text (quote reasons, match exceptions, assistant answers, stored audit summaries) stays English |
| Dates, numbers and ¥ by locale | ✅ | Vitest: `￥1,234,567`, ja-JP dates, month names, percentages | — |
| Language switcher saved per user | ✅ | API tests (`users.locale`, `PATCH /auth/me/preferences`); Vitest: saved to profile when signed in, device only when not | — |
| Backend error codes mapped to translated messages | ✅ | Vitest: reason code → field-aware message; code fallback; network errors; validation messages by Pydantic type | — |
| *Found:* P2P page hid error reasons; landing page understated what is built | ✅ | Fixed in the translated pages | — |

Full check list for this phase: ruff, format, mypy, migrations up/down/up on the populated test DB +
`alembic check`, **211 backend tests**, `gen_docs --check`, `check_doc_claims`, `pip-audit` (none),
`oxlint`, build, `npm audit` (0), **56 frontend tests**. The first run failed only `ruff format --check`
(one test file), fixed before commit.

## Phase 2: notifications, outbox and worker (branch `gaps/phase-2-notifications`, notes: [prs/phase-2.md](prs/phase-2.md))

| Gap | Status | Verified by | Remaining |
|---|---|---|---|
| Transactional outbox written with the state change (routed, escalated, delegated, rejected, PO sent, invoice mismatch, payment awaiting approval; plus approved) | ✅ | Integration tests: row written with the submission, none for a blocked one, rolls back with its transaction; one test per event source | — |
| Worker `python -m app.worker`: SKIP LOCKED claims, backoff, dead letter after N, idempotent via delivery key; PostgreSQL only (ADR-013) | ✅ | Two concurrent workers + full replay create no duplicates; backoff → dead letter → admin retry → sent; real worker CLI run end to end | No retention job for old outbox/delivery rows |
| In-app notifications (bell + list) | ✅ | API tests; Vitest (count, en/ja wording from stored facts, mark read) | — |
| E-mail over SMTP, Mailpit in docker-compose | ✅ | aiosmtpd test (Japanese subject/body); end-to-end run: API → outbox → worker process → Mailpit | Not used with a real mail provider (needs your SMTP credentials) |
| Slack and Teams incoming webhooks per tenant | ✅ | URL allow-list (SSRF cases), encrypted at rest, never returned; Slack text and Teams Adaptive Card payloads; HTTP transport against a local server | Not posted to a real Slack workspace or Teams tenant (needs your webhooks) |
| Users choose channels | ✅ | Muted events and e-mail opt-out respected (API + Vitest) | — |
| Worker in docker-compose.stack.yml, render.yaml (in-process or paid worker) and docs | ✅ | `docker compose config` valid; YAML parses; Render free plan has no workers, so in-process + hourly cron `--once` | A dedicated Render worker needs a paid plan (left commented) |
| RLS test per new table as the runtime role | ✅ | All five tables: tenant-scoped, fail-closed without context, cross-tenant insert rejected; no DELETE on history | — |
| *Found:* audit rows had NULL entity ids for created invoices / POs / knowledge versions | ✅ | UUIDs now assigned at construction; test asserts entity ids present | — |

Full check list: ruff, format, mypy, migrations round trip + `alembic check`, **245 backend tests**,
`gen_docs --check`, `check_doc_claims` (13 ADRs), `pip-audit` (none), `oxlint`, build (main bundle
302.42 kB), `npm audit` (0), **60 frontend tests**: all passed on the first run.

## Phase 3: invoice file storage (branch `gaps/phase-3-invoice-storage`, notes: [prs/phase-3.md](prs/phase-3.md))

| Gap | Status | Verified by | Remaining |
|---|---|---|---|
| S3-compatible storage through an interface (MinIO local/CI, S3/R2 prod, behind config) | ✅ (MinIO substituted) | S3 test against RustFS 1.0.5 (write-once, SigV4 presigned URL, UTF-8 filename); local backend tests | MinIO no longer publishes community images, so RustFS is the stand-in; not run against AWS S3 or R2 (needs your account) |
| `invoice_documents` (tenant RLS): key, SHA-256, size, MIME, pages, uploader, scan status, linked to invoice; immutable once linked | ✅ | Trigger blocks identity changes, re-link and delete even for the owner; runtime role has no DELETE; RLS tests | — |
| Keep magic-byte, size and page limits | ✅ | Unit tests per rejection reason; real PDF page count (pypdf) | — |
| Optional ClamAV scan through the worker (pending / clean / infected) | ✅ | clamd protocol stand-in (clean + infected); **real ClamAV 1.4**: clean PDF clean (direct and upload → worker), raw EICAR detected | EICAR cannot be embedded in a valid upload, so the infected API path relies on the stand-in |
| Infected or unscanned files cannot back a payable invoice | ✅ | Payment request and approval refused for infected / pending / not-scanned (unless explicitly allowed) | — |
| Downloads through the API with a permission check (short-lived presigned URLs) | ✅ | 60 s SigV4 presign or streamed with hash re-check; permission and tenant tests; infected/pending blocked | — |
| Retention setting in config and docs | ✅ | `PROCURAX_DOCUMENT_RETENTION_YEARS` (7) recorded per file; deployment guide | No automatic deletion; bucket versioning/object lock recommended |
| *Found:* boto3 presigned with SigV2 against custom endpoints | ✅ | Forced SigV4; test asserts `X-Amz-Signature` | — |

Full check list: ruff, format, mypy, migrations round trip + `alembic check`, **271 backend tests passed,
1 skipped** (the opt-in real-ClamAV test, run separately and passing), `gen_docs --check`,
`check_doc_claims` (14 ADRs), `pip-audit` (none), `oxlint`, build, `npm audit` (0), **60 frontend tests**.

## Phase 4: Japan compliance (branch `gaps/phase-4-japan-compliance`, notes: [prs/phase-4.md](prs/phase-4.md))

| Gap | Status | Verified by | Remaining |
|---|---|---|---|
| NTA registry Web-API adapter behind a flag (needs an application ID) | 🟡 | Real-HTTP mock of the v1 `num` endpoint: API call with the application ID, cache hit, revoked/not-found → exception, outage → local checks or stale cache; production guard | **Not called against the real NTA API** (needs your application ID); parameters/field names to confirm live |
| Cache results with a timestamp; record evidence on the invoice match; fall back to the check digit | ✅ | `qualified_invoice_registry_cache` (fetched_at); `match_details.qualified_invoice.registry` (source, status, name, dates); fallback tests | — |
| No scraping | ✅ | Only the official Web-API endpoint is called | — |
| Peppol / JP PINT UBL import into the invoice + line model, same three-way match | ✅ | JP PINT sample (10 % + 8 % lines) imports and matches; price mismatch, currency, order, line reference, unsafe XML (billion laughs, XXE) and encoding cases | Full Schematron validation left to an access point |
| Sample files in tests/fixtures | ✅ | `tests/fixtures/einvoice/*.xml`, `tests/fixtures/nta/*.json` | — |
| Document what a Peppol access-point integration needs | ✅ | `docs/einvoicing.md` | Sending over Peppol is out of scope (as the brief says) |

Full check list: **294 backend tests passed, 1 skipped** (opt-in real ClamAV), **60 frontend tests**,
migrations round trip + `alembic check`, `gen_docs`, `check_doc_claims` (15 ADRs), `pip-audit`,
`npm audit`, `oxlint`, build: all passed on the first run.

## Phase 5: contracts, multi-currency, ERP journal export (branch `gaps/phase-5-contracts-fx-erp`, notes: [prs/phase-5.md](prs/phase-5.md))

| Gap | Status | Verified by | Remaining |
|---|---|---|---|
| Contracts table with lifecycle (draft → active → expired / terminated), value cap, signed file | ✅ | Unit: state machine and coverage; API: lifecycle, permissions, edit lock, file checks and lock after activation; RLS test as the runtime role | No date-effective amendments (terminate and replace) |
| Policy uses contract records (VEN-004) with the vendor field as fallback; cap overrun routes procurement (CON-001) | ✅ | Unit: CON-001 within / over / no cap / cap currency without a rate / switched off; API: draft ignored, active covers, overrun routed, terminated → expired | — |
| Expiry and renewal reminders through the worker | ✅ | API: sweep sends one `contract.expiring` event (en/ja templates) and expires after the end date; SKIP LOCKED claim | — |
| Multi-currency: rates table, rate + base amount snapshotted at submission, VAL-004 only without a rate | ✅ | Unit: rate choice, manual precedence, rounding; API: blocked without a rate, ¥150,250 from $1,000 at 150.25, correction does not move the snapshot | — |
| Budgets, policy, analytics on base amounts | ✅ | API: budget pending in base currency; existing analytics and budget tests pass on `base_amount` (backfilled by migration 0018) | — |
| ECB reference-rate adapter (free, no key), off by default | 🟡 | Parser tested on a fixture in the ECB file format (and against hostile XML); refresh endpoint tested with the fetch replaced by the fixture | **Not fetched live** from the ECB; nothing refreshes automatically |
| ERP journal export: balanced entries, CSV / JSON, each document once | ✅ | Unit: entries balanced, mapping precedence, tax line, payment entry, CSV injection, JSON; API: export, second export refused, byte-identical download; `journal_exports` insert-only for the runtime role | File only; pushing to an ERP API (via the outbox) is not built |
| RLS test per new table | ✅ | `contracts`, `exchange_rates`, `gl_account_mappings`, `journal_exports`: scoped to the tenant, nothing without context, cross-tenant insert rejected | — |
| SPA screens in en / ja | ✅ | Vitest: contracts, exchange rates, journal export; locale tests (same keys, placeholders, rule IDs = generated catalogue) | Native-speaker review of the new Japanese wording |
| *Found:* policy evidence was formatted in the request currency | ✅ | Request detail now formats rule evidence in the base currency | — |

Full check list: **324 backend tests passed, 1 skipped** (opt-in real ClamAV), **66 frontend tests**,
migrations round trip + `alembic check`, `gen_docs`, `check_doc_claims` (18 ADRs, 22 rules), `pip-audit`,
`npm audit`, `oxlint`, build: all passed; `ruff format` flagged one new test file on the first run, fixed
and re-checked.

## Phase 6: security hardening (branch `gaps/phase-6-security`, notes: [prs/phase-6.md](prs/phase-6.md))

| Gap | Status | Verified by | Remaining |
|---|---|---|---|
| Refresh token in an HttpOnly cookie, in-memory access token, rotation and revocation | ✅ | API: cookie attributes, rotation, reuse detection revokes the family, revoked session ends its access token, sign-out, revoke others, deactivation; Vitest: memory-only token, renewal on 401, resume after reload | — |
| CSRF protection for cookie endpoints | ✅ | API: missing/wrong `X-CSRF-Token` → 401, foreign `Origin` → 403 | — |
| TOTP MFA with recovery codes | ✅ | RFC 6238 vectors; API: enrolment, two-step sign-in, replay refused, recovery code single use, throttled wrong codes, admin reset; Vitest: sign-in code step, Security page | Opt-in per user; no per-organisation enforcement, no WebAuthn |
| OIDC SSO | 🟡 | Full code + PKCE flow against a local provider stand-in (real HTTP, RS256 ID tokens): success and seven refusal cases | **Not run against a real identity provider** (needs your IdP tenant and client credentials) |
| Rate limiting shared through PostgreSQL | ✅ | Two throttle instances share counts; login and MFA throttles over HTTP; keys stored hashed | Per-address keys rely on forwarded headers that clients can forge (per-account limit still applies) |
| docs/security.md (threat model) | ✅ | Controls listed with test status; STRIDE with residual risks | No external penetration test |
| RLS test per new table | ✅ | `auth_sessions`: tenant-scoped, nothing without context, cross-tenant insert rejected, no DELETE | `auth_throttle` holds no tenant data (no RLS, documented) |
| Live deployment topology for the cookie | 🟡 | Netlify now proxies `/api/*` to Render so the cookie is first-party; Vite and nginx set-ups already same-origin | **Not verified on the live site** until you deploy |
| *Found:* Phase 5 reason codes had no Japanese wording | ✅ | Added with the Phase 6 codes; locale tests pass | — |

Full check list: **353 backend tests passed, 1 skipped** (opt-in real ClamAV), **76 frontend tests**,
migrations round trip + `alembic check`, `gen_docs`, `check_doc_claims` (21 ADRs), `pip-audit`, `npm audit`,
`oxlint`, build: all passed on the first full run.

## Phase 7: observability and operations (branch `gaps/phase-7-observability`, notes: [prs/phase-7.md](prs/phase-7.md))

| Gap | Status | Verified by | Remaining |
|---|---|---|---|
| `/metrics` (Prometheus) | ✅ | API tests (labels, counters, DB-read gauges, token rules); real scrape by Prometheus in the Docker stack | No hosted metrics backend for the live demo (needs an account) |
| OpenTelemetry behind a flag | ✅ | In-memory exporter test (HTTP and SQL spans, trace id in logs); real traces from API and worker in Jaeger | Off by default; no hosted collector |
| Grafana / Prometheus with dashboards and alerts | ✅ | Stack check: dashboard provisioned and answering a query; 8 alert rules loaded ([report](observability_check.json)) | Thresholds are starting points, no paging integration |
| Backups and a real restore drill | ✅ drill / 🟡 nightly job | Real drill: 39 tables, 299 rows, all six checks passed ([report](restore_drill.json)); CI runs the drill on every push | The nightly encrypted backup workflow has not run (needs your owner URL and a passphrase) |
| Runbook | ✅ | `docs/runbook.md`: a section for every alert, restore, deploy/rollback, secret rotation | On-call contacts to be filled in |
| *Found:* SQLAlchemy instrumentation silently inactive on SQLAlchemy 2.1 | ✅ | Version check skipped with a comment; test asserts SQL spans | — |

Full check list: **357 backend tests passed, 1 skipped** (opt-in real ClamAV), **76 frontend tests**,
migrations round trip + `alembic check`, `gen_docs`, `check_doc_claims` (23 ADRs), `pip-audit`, `npm audit`,
`oxlint`, build: all passed on the first full run.

## Phases 8–10

Not started. Rows are added when each phase begins.
