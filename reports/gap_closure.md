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

## Phase 1: missing screens and Japanese UI

| Gap | Status | Verified by | Remaining |
|---|---|---|---|
| Delegation screen | not started | | |
| Vendor bank account screen | not started | | |
| Payer payment-settings screen | not started | | |
| Japanese UI (i18n, locale formatting, switcher saved per user, error codes mapped) | not started | | |

## Phases 2–10

Not started. Rows are added when each phase begins.
