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

## Phases 4–10

Not started. Rows are added when each phase begins.
