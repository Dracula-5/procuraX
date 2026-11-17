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

## Phases 2–10

Not started. Rows are added when each phase begins.
