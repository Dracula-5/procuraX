# Phase 0: make the docs match the code

Branch: `gaps/phase-0-docs`

## What changed

- **Permission count.** The P2 stage report said 24 permissions; `app/domain/rbac.py` defines 23
  (10 roles). Corrected in `docs/transformation_roadmap.md`.
- **Render region.** `render.yaml` said `singapore`; the measured smoke run
  (`reports/live_deployment_smoke.json`) and the Neon steps in `docs/deployment.md` said Oregon.
  The owner confirmed the live service (`procurax-mod8.onrender.com`) is in **Oregon**.
  `render.yaml` now says `region: oregon`, with a note that Render fixes a region at creation and
  how to move to Singapore. `docs/deployment.md` states the live region.
- **pgvector wording.** `docs/system_design.md` described pgvector retrieval as if it existed. No
  migration creates the extension and no table has a vector column; only the
  `pgvector/pgvector:pg16` image is used. The architecture diagram, §7, the technology table and
  ADR-002 now say "image only until Phase 8".
- **CI check on doc counts.** New `app/scripts/check_doc_claims.py` compares every "N roles",
  "N permissions", "N rules" and "N ADRs" phrase in `README.md` and `docs/*.md` (generated docs
  excluded) with `rbac.py`, the rule catalogue and the ADR headings. A line containing
  "at the time" is a dated historical statement and is skipped; the P0–P3 stage report's counts are
  marked that way so later phases can add rules and ADRs without rewriting history. Added as a CI
  step after `gen_docs --check` and to the README check list.
- README check list now shows the full migration round trip, as CI runs it.
- **Data-unsafe downgrade fixed (pre-existing).** Running the check list twice locally failed at
  `alembic downgrade base`: migration 0007's downgrade re-added the narrower
  `invoices.match_status` CHECK while rows still held `exception_approved`/`exception_rejected`.
  CI never saw it because it only round-trips an empty database. The downgrade now maps those rows
  to `requires_review` first (as 0012 already does for `exported` payments), and CI gains a second
  round trip after the tests, on the populated database.
- **Dependency audits.** `pip-audit` reported `dnspython 2.8.0` (via email-validator) and
  `opentelemetry-api 1.45.0` (via FastAPI); both upgraded in `uv.lock` (2.9.2, 1.45.2). `npm audit`
  reported Vite 8.3.0–8.3.2 dev-server advisories, several Windows-specific; `npm audit fix` moved
  the lockfile to Vite 8.3.8. CI's `pip-audit` step would have failed on the baseline.
- Roadmap: new "Gap-closure programme" table tracking Phases 0–10.

## How it was tested

- `check_doc_claims` passes on the corrected docs. On the original docs it fails with
  `docs/transformation_roadmap.md:167: says 24 permissions, the code has 23`.
- `tests/unit/test_doc_claims.py`: the repository passes; counts come from the sources of truth;
  wrong counts are reported with file and line; qualified claims ("20 policy rules") are checked;
  historical lines and unrelated phrases ("3-way match rules") are ignored; `docs/generated/` is
  not scanned.
- Full check list (local, PostgreSQL 16 pgvector image on Docker Desktop, Windows): ruff, ruff format,
  mypy, migrations up/down/up + `alembic check`, **173 passed** (`pytest --cov`, 5 min 41 s),
  `gen_docs --check`, `check_doc_claims`, `pip-audit` (no known vulnerabilities), `oxlint`,
  `npm run build`, `npm audit` (0 vulnerabilities). The first run failed at `alembic downgrade base`
  on the populated database (above); after the fix, downgrade to base succeeded with 16 invoices
  and 56 requests present, followed by upgrade to head and `alembic check` (no drift).
- `npm test` and `npm run test:e2e` do not exist yet (Phases 1 and 9 add them).

## Still open

- None for this phase. The region note assumes the owner's confirmation; Render's dashboard was not
  inspected from here.
