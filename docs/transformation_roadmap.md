# Transformation roadmap

Two views of the same plan:

- **Transformation phases**: how a client organisation would adopt the platform, in the order
  that creates value and de-risks the next step.
- **Delivery stages (P0–P22)**: the engineering sequence for building it, with evidence per
  stage.

Legend: ✅ Implemented · 🟡 Partially implemented · ⬜ Future roadmap

## Transformation phases (client adoption view)

| Phase | Outcome for the client | Status | Evidence / what is missing |
|---|---|---|---|
| 1. Digital procurement intake | Every request enters through one form with line items, cost centre and justification | ✅ | Purchase requests, drafts, numbering, UI form |
| 2. Approval workflow | Policy-driven routing; inbox; SLA; audit | 🟡 | Routing, inbox, due dates, audit, delegation and SLA escalation to the next eligible manager. The sweep runs inside the API on an interval (advisory-locked, so replicas never double-escalate) or from an external scheduler. External email/chat notifications remain. |
| 3. Vendor management | Vendor master with onboarding control, risk and contract status | 🟡 | Onboarding and status control ✅. Performance/spend history ⬜ (needs PO/invoice data). |
| 4. PO / invoice digitisation | POs from approved requests; receipts; invoice capture | 🟡 | POs use the supplier's quoted unit price per line (or apportion a total-only quote). Line-specific receipts and invoice lines. Local Tesseract OCR runs end to end; a synthetic rendered-invoice benchmark is recorded. Invoice files are not persisted; vendor portal and e-invoicing (Peppol/JP PINT) remain. |
| 5. 3-way matching | Automated PO ↔ receipt ↔ invoice matching with an exception queue | 🟡 | Per-line price check; quantity checked against accepted quantity **not yet invoiced** (blocks double billing, serialised by a row lock); Japan consumption-tax check per rate with lawful rounding; qualified-invoice registration number check digit and vendor-master match; duplicate block; finance exception approve/reject; close requires every accepted quantity invoiced. Payment execution and a dedicated reconciliation UI remain. |
| 6. Spend analytics | Real-time spend by department, vendor and category; KPIs | 🟡 | Live filtered approved spend, P2P states, 12-month trend, mean/median/P90/max decision cycle, CSV export and current-FY budget utilization by cost centre delivered. Savings opportunities and anomaly review remain. |
| 7. AI vendor ranking | Recommendations with evidence, benchmarked against rules | 🟡 | Transparent weighted score over eligible quotes is shown and recorded at award; overriding it needs a reason; outcomes (on-time, damage, first-invoice match) are reported against followed vs. overridden awards. Pairwise LTR evaluated on synthetic preferences only; real outcome history required. |
| 8. AI document intelligence | Invoice field extraction with confidence, benchmarked against OCR rules | 🟡 | Tesseract 5.5.3 + labeled-line parser measured on rendered synthetic invoices (clean / degraded scan / low resolution / PDF) with a held-out seed; an OCR-tolerant clean-up mode (check-digit-confirmed registration repair) is compared with the strict parser. No real or Japanese-language invoices, confidence scores or document-AI comparison yet. |
| 9. Procurement assistant | Grounded answers with citations; safe analytics queries | 🟡 | Fixed allow-listed data intents plus cited BM25 retrieval over policy rules and tenant documents, now including Japanese text (CJK bigrams). Official NTA Q&A (171 questions, real data): Recall@5 0.158 → 0.906, MRR@10 0.138 → 0.793. A screen to add guidelines and search with citations. Semantic/hybrid retrieval and broader analytics questions remain. |
| 10. Enterprise integration | ERP, SSO, e-mail, object storage, payment sandbox | ⬜ | Designed (system_design §8); nothing integrated yet |

Why this order: phases 1–3 create the **structured, trustworthy data** (requests, decisions,
vendors, budgets) that phases 6–9 depend on. Adding AI before intake is digitised would mean
training on e-mail threads and spreadsheets.

## Delivery stages

| Stage | Scope | Status |
|---|---|---|
| P0 | Repository & product specification | ✅ |
| P1 | Core procurement workflow | ✅ |
| P2 | Authentication, RBAC, tenants | ✅ (SSO/MFA/refresh cookies deferred to P18) |
| P3 | Database & APIs | ✅ |
| P4 | Public deployment | 🟡 **Live on free tiers:** web https://procurax.netlify.app (Netlify), API https://procurax-mod8.onrender.com (Render Docker, prod settings, public-demo mode) with Neon PostgreSQL. CI builds the image; the Deploy workflow migrates as the owner role. Remaining: database migration and demo seed run once the GitHub database secrets are added |
| P5 | Approval & policy engine: delegation, escalation job, notifications | 🟡 Delegation/revocation; SLA escalation scheduled in-process (advisory lock) or externally; external notifications remain |
| P6 | Quote capture/comparison, PO lifecycle, receipts + invoice capture | 🟡 Eligibility-aware weighted baseline; optional supplier line prices flow exactly into PO lines; line receipts and invoice lines; OCR runs end to end with a recorded synthetic benchmark |
| P7 | 3-way matching, duplicate invoice rules, payment approval | 🟡 Cumulative line matching, tax and registration checks, exception decisions, close rules, payment approval and Zengin 総合振込 transfer-file export (row-locked, once per payment) delivered; ProcuraX never moves money |
| P8 | Spend analytics & executive dashboard | 🟡 Live category/department/vendor and P2P aggregates; date, category, department and vendor filters; 12-month trend; mean/median/P90/max cycle times; CSV export and current-FY budget utilization by cost centre delivered. Savings opportunities and anomaly review remain |
| P9 | Baseline ML models (spend classification first) | 🟡 Offline TF-IDF + logistic regression baseline and reproducible 1,000-row synthetic smoke evaluation delivered; real labeled data and model comparison remain |
| P10 | Vendor recommendation (rules vs LTR) | 🟡 Dependency-free pairwise LTR and ranking metrics delivered; comparison evaluated only on synthetic preferences. Real labeled outcomes remain |
| P11 | Invoice AI (OCR rules vs document AI) | 🟡 Strict vs OCR-tolerant parser measured end to end with Tesseract on 2×40 rendered synthetic invoices per condition (dev + held-out seed). Real labelled documents, Japanese layouts, tables, confidence and a document-AI candidate remain |
| P12 | Anomaly detection | 🟡 Explainable category-level robust log-MAD amount baseline and synthetic injection smoke evaluation delivered; vendor/time/split signals and real labels remain |
| P13 | Procurement RAG | 🟡 Japanese-capable lexical retrieval measured on real data (official NTA Q&A, 171 questions): Official NTA Q&A (171 questions, real data): Recall@5 0.158 → 0.906, MRR@10 0.138 → 0.793. Chunking bug fixed. Embeddings, reranking and paraphrased-question evaluation remain |
| P14 | Procurement assistant (text-to-query) | 🟡 Fixed supported intents, ORM-only parameterized queries, evidence and unsupported-query refusal delivered; free-form planning remains |
| P15 | Human-in-the-loop recording (AI suggestion vs decision vs outcome) | 🟡 Sourcing awards record the recommendation shown, the decision and an override reason; `/decision-support/sourcing` reports agreement and delivery/invoice outcomes by followed vs. overridden. Other AI surfaces (classification, anomaly) not yet recorded |
| P16 | Real-user pilot | ⬜ Not run (no participants). [Illustrative personas and demo walkthroughs](user_personas.md) document the intended users and the protocol a pilot would follow; no adoption claims |
| P17 | Load testing | 🟡 Standard-library read-only HTTP load harness and sample request plan delivered; 50–500 user API/database runs and host/resource measurements remain |
| P18 | Security hardening | 🟡 Production config guards, security headers (API + nginx CSP), log redaction, failed-login throttling (single process) and CI dependency audit delivered; shared-store rate limiting, penetration test and deployment review remain |
| P19 | Observability (metrics, dashboards, tracing) | 🟡 Structured logs, request IDs, health/ready ✅ |
| P20 | Business value measurement & cost model | Partial: parameterized formulas and an assumption-labeled calculator delivered; no project scenario populated or savings claimed |
| P21 | Final documentation (consulting case, final benchmark) | 🟡 Consulting case and evidence-gated benchmark report drafted |
| P22 | Resume & interview preparation | 🟡 Interview guide drafted; resume bullets limited to verified engineering facts |

## Additional delivery evidence (P6–P8 first slice)

- A procurement officer can issue one PO from an approved request with an approved preferred vendor. Duplicate PO creation is prevented, and creation is audited.
- Procurement can record multiple supplier quotes and compare a transparent weighted rule baseline (price 40%, delivery 20%, quality 25%, contract compliance 15%) with reasons. This is a decision aid, not a learned model or automatic award.
- PO transitions include approved → sent → acknowledged → partial/received → closed. Cancellation requires a reason and is blocked after a receipt exists.
- Receipt capture checks the remaining order quantity while holding a PO row lock; accepted and damaged quantities are retained.
- Procurement records awarded quote decisions and generates POs at the awarded total, allocated across request lines. Line-specific receipts and invoice lines are matched against each PO line's price and accepted quantity; tax tolerance is not implemented. Duplicate vendor + invoice numbers are rejected case-insensitively.
- `GET /spend-analytics` aggregates tenant-scoped approved request value by category, department and vendor, PO and invoice match state, payment state, 12-month approved-spend trend and decision-cycle mean/median/P90/max. Optional date, category, department and vendor filters apply across the related request, PO, invoice and payment summaries; the dashboard exports the active result as CSV. It makes no before/after or savings claims.
- The assistant's knowledge endpoint reads the tenant's active approval policy, versioned tenant text documents and the rule catalogue using BM25 lexical ranking. Admins can add new document versions or deactivate sources; citations include document version and chunk. It returns excerpts, not generated answers.
- A scheduled SLA command reassigns overdue named approval steps to the next eligible manager, extends the due time by the active policy SLA and writes an audit event. Run `python scripts/run_sla_escalations.py` from `backend/` on a local schedule; email/chat notifications are not connected.
- The local OCR adapter accepts bounded PDF/image input, uses temporary files with Tesseract and `pdftoppm`, then passes text through the labeled-line parser. With Tesseract 5.5.3 from conda-forge it now runs end to end; see the P11 benchmark.
- P10's pairwise logistic ranker is evaluated against the weighted baseline on generated preference groups. P13's BM25 retrieval has an exact-term synthetic corpus smoke benchmark. Both results only validate metric plumbing.
- Offline P9/P12 scripts produce explicitly synthetic benchmark artifacts. The template-generated classification set scores 1.00 accuracy/macro-F1 and the injected amount-outlier set scores 1.00 precision/recall/F1 with 0 false positives; these intentionally easy synthetic smoke fixtures are not estimates of real-world performance.
- The Purchase to pay page exposes the approved request queue, order states and invoice match queue. Purchase requests use the tenant base currency.
- The seven new tenant tables use forced RLS and explicit runtime-role grants. Duplicate detection is scoped to tenant and vendor.
- A matched invoice can enter a payment approval queue. Only finance managers receive the new payment approval permission; self-approval is blocked and the decision is audited. This records approval only; no money is transferred.
- Production settings reject demo mode, SQL echo, insecure public URLs, wildcard/non-HTTPS CORS origins and the development JWT key. Structured log extras redact common credential field names.

This remains a partial procure-to-pay implementation. Quotes without supplier line prices are still apportioned across request lines. Persistent invoice-file storage, integrated PO dispatch/vendor portal, NTA registry lookup and payment execution are absent.

### Cross-check status

- Frontend production build (`tsc -b` + Vite): passed after the P6–P8 UI change.
- Ruff, formatter and mypy: passed using the project venv.
- PostgreSQL migration upgrade and `alembic check`: passed through head; no schema drift.
- P2P API tests cover the quote comparison, approval transitions, receipt/invoice match, duplicate invoice, payment self-approval guard and tenant-scoped list results.
- Security unit tests cover production configuration guards and recursive structured-log redaction; assistant tests cover tenant policy lookup and cited policy-rule retrieval.
- Local load-run instructions and a dependency-free HTTP harness are in [local_verification.md](local_verification.md); no representative load result is claimed.
- A five-worker, two-second `/health` smoke run returned 612/612 HTTP 200 responses (304.63 requests/s, p50 13.05 ms, p95 31.83 ms, p99 37.04 ms) on the local Windows host. This excludes authentication, database work and business endpoints.

## Stage report: free deployment, Japanese retrieval, bank transfer export (2026-10-01, second increment)

### Completed
- **Public demo mode.** `PROCURAX_PUBLIC_DEMO` is the only way production accepts persona login. It turns off
  self-registration, adds a UI banner and a `/meta` endpoint, and the UI shows a wake-up notice for sleeping free-tier APIs.
- **Free deployment kit.** `render.yaml`, Neon instructions, `deploy.yml` (migrate as owner, then deploy hooks)
  and `demo-maintenance.yml` (hourly SLA sweep, nightly reset). The API image now includes Tesseract (eng + jpn)
  and Poppler and binds to `$PORT`.
- **Japanese retrieval.** NFKC normalisation and CJK character bigrams, measured on the NTA's official Q&A. A
  guideline upload and cited search screen were added to the assistant.
- **Zengin transfer file.** Vendor bank accounts and payer settings are validated into bank characters. Export
  takes approved JPY payments under row locks, keeps a 120-byte record snapshot per payment, and audits the file hash.
- **Personas.** Illustrative personas and demo walkthroughs, clearly labelled.

### Defects found and fixed
- The chunker emitted about 120 near-duplicate tail fragments per document (2,000 characters → 123 chunks instead of 3).
- `seed_demo --reset` crashed: its teardown list was missing seven tables added in later stages.
- On a managed database whose owner is not a superuser, the demo reset would have seen no rows (no RLS bypass).
- `python scripts/run_sla_escalations.py`, the documented command, failed with `No module named 'app'`.

### Measured
| What | Result | How |
|---|---|---|
| Backend tests | 165 passing, 92% line coverage | Local PostgreSQL 16 (superuser owner) |
| Same suite, Neon-like owner | 163 passing | Owner `NOSUPERUSER NOBYPASSRLS`, after migrations, seed and two resets |
| Japanese retrieval (real data) | Official NTA Q&A (171 questions, real data): Recall@5 0.158 → 0.906, MRR@10 0.138 → 0.793 | [final_benchmark.md](../reports/final_benchmark.md#japanese-retrieval-on-the-official-nta-qa) |

### Problems / limits
- Render and Neon were not exercised (they need the owner's accounts); the rehearsal approximates Neon's role model only.
- Free tier: the API sleeps after 15 idle minutes, so the first request takes about a minute; scheduled GitHub workflows pause after 60 idle days.
- Not done: OCR on a public receipt set (CORD) and a delegation screen.

### Next stage
Create the three free accounts and follow [deployment.md](deployment.md).

## Stage report — P4–P7, P11, P15, P18 increment (2026-10-01)

### Completed
- **Cumulative three-way match.** An invoice line may bill only the accepted quantity not yet billed on earlier, non-void invoices. Previously a second invoice for already-billed goods at the PO price was marked *matched* (double-billing risk). Invoices on one order are serialised by a row lock; a concurrent-submission test proves only one of two identical invoices matches.
- **Close rules.** A rejected exception no longer blocks closing forever; closing now requires every accepted quantity to be invoiced.
- **Consumption tax (消費税).** When rates are stated per line (10% standard / 8% reduced), declared tax must fall between per-rate floor and ceiling totals, matching the Qualified Invoice System's once-per-rate rounding.
- **Qualified invoice registration (インボイス制度).** Invoice registration numbers must pass the corporate-number check digit and equal the vendor master; tax without a number is flagged. The NTA public registry is not queried.
- **Quote line pricing.** Suppliers' per-line unit prices are validated against the quote total and copied exactly to PO lines; total-only quotes are apportioned as before. The PO records its pricing basis.
- **Human-in-the-loop record (P15).** Ranking excludes ineligible quotes; the award stores the recommendation shown, the human decision and a mandatory override reason; an analytics endpoint and dashboard card report agreement and outcomes.
- **Scheduled escalation.** In-process interval sweep guarded by `pg_try_advisory_xact_lock`; the CLI uses the same lock.
- **OCR.** Tesseract runs end to end; benchmark below. OCR-tolerant parsing is used by the extraction endpoint.
- **Deployment readiness (P4).** Container stack with release-step migrations, verified locally.
- **Security.** Failed-login throttling per address + e-mail with `Retry-After`.
- **Fixed pre-existing defects.** `alembic upgrade head` failed on an empty database (duplicate permission seed in 0004; a 0010 rename of a constraint fresh installs never had); 0005's downgrade dropped double-prefixed constraint names, breaking CI's round trip; a double-prefixed check-constraint name is normalised in 0011; UI and docs text was double-encoded UTF-8 (`Â·`, `â€”`) in 30+ files; a ruff violation; a README port typo; an `.env.example` that set an empty JWT secret.

### Measured
| What | Result | How |
|---|---|---|
| Backend tests | 152 passing, 92% line coverage | `pytest --cov` against local PostgreSQL 16 |
| Migrations | Empty DB → head → base → head, then `alembic check`: no drift | Test database |
| Invoice OCR (held-out synthetic, 40 invoices/condition) | Avg field accuracy strict → OCR-tolerant: clean 0.9725 → 0.9900; degraded scan 0.6225 → 0.8075; low resolution 0.7100 → 0.7500. Wrong-value rate: 2.75% → 1.00%, 19.25% → 6.00%, 17.25% → 9.00% | [invoice_ocr_synthetic.json](../reports/invoice_ocr_synthetic.json); Tesseract 5.5.3, local Windows host |
| Container stack | Built from source; migrate job exited 0 on an empty database; API healthy with prod settings; through nginx: SPA and deep link 200, CSP/X-Frame headers present, tenant registration and `/auth/me` succeeded, demo login 404, five failed logins then 429 with `Retry-After` | `docker compose -f docker-compose.stack.yml up --build` |

### Problems
- The OCR clean-up rules were written after inspecting dev-seed errors, so only the held-out seed is quoted; both are still synthetic, English-labelled single-column renders.
- The login throttle is per process; multiple replicas need a gateway or shared-store limit.
- The decision-support sample in any tenant will be small; it reports counts without significance claims.

### Next stage
P4 public hosting (needs the owner's platform choice and accounts), then a genuine pilot (P16) to populate real decision outcomes and a labelled invoice set.

## Stage report — P0 to P3

### Completed
- **P0:** product positioning, business problem (current state, root causes, KPIs, risks,
  assumptions), product spec, system design with 11 ADRs, this roadmap, and reference docs
  generated from code (RBAC matrix, policy rules, OpenAPI).
- **P1:** purchase-request lifecycle as an explicit state machine; deterministic policy engine
  with 21 rules (validation, vendor, contract, value tiers, budget, split purchase,
  emergency); sequential approval chains from the org structure and role pools; segregation of
  duties; no double sign-off; SLA due dates; resubmission rounds; budget re-check under lock at
  final approval; append-only audit trail; per-request timeline; dry-run policy preview.
- **P2:** tenant registration, invitation-based onboarding (hashed single-use tokens),
  Argon2id, JWT, 10 roles and 24 permissions with the code as source of truth, last-admin and
  reporting-cycle guards, platform-admin not grantable from tenants, fictional demo tenant with
  persona login.
- **P3:** PostgreSQL schema (17 initial tables) via Alembic; RLS with FORCE on every tenant
  table; least-privilege runtime role; React SPA covering intake, approvals, vendors, budgets,
  policy, audit, users and org structure.
- **P6–P8 first slice:** tenant-scoped quote award, PO lifecycle, line-specific receipts, invoice
  line matching and exception review, duplicate block, finance payment approval, spend summaries
  and Purchase to pay SPA page.

### Measured (engineering facts only; no business metrics exist yet)
| What | Result | How |
|---|---|---|
| Automated tests | 130 passing at the time of that report (now 152; see the latest stage report) | Project venv, PostgreSQL test DB, `pytest --cov` |
| Line coverage (backend `app/`) | 91% | `pytest --cov` with greenlet-aware tracing |
| Static checks | ruff, ruff format, mypy: clean | Project venv |
| Migration | Upgrade through head; `alembic check`: no schema drift | Local PostgreSQL |
| Frontend bundle | 427.22 kB JS (124.79 kB gzip), 23.25 kB CSS | `npm run build` |

No representative product-capacity, business or adoption claims are made. Synthetic model smoke results and the `/health` liveness measurement are implementation checks only; production metrics require real data and a pilot.

### Tests
- **Unit:** policy thresholds at exact boundaries, step ordering and dedupe, every vendor,
  budget, split and emergency rule, determinism, state-machine reachability and illegal
  moves, RBAC invariants (admin cannot approve, analyst read-only, platform admin isolated),
  fiscal year and money rounding.
- **API:** full workflows including rejection → reopen → resubmit, policy block → fix, budget
  exceeded → finance, **concurrent final approvals cannot overspend**, split purchase,
  emergency, cancellation rules, visibility by reporting line, inbox correctness, stats from
  live data, policy versioning (old decisions keep their version), role guards on every
  surface, error envelope and request IDs.
- **Tenant isolation:** through the API across all resource types, plus directly against
  PostgreSQL as the runtime role (RLS fail-closed, cross-tenant write rejected, audit log
  immutable, RBAC catalogue read-only and in sync with code).

### Problems found and fixed during these stages
| Problem | Fix |
|---|---|
| Alembic silently drops `use_alter` FKs inside `create_table` | Created explicitly after both tables |
| Replacing line items violated `(request, line_no)` uniqueness (the ORM inserts before it deletes) | Update in place by position; add or remove at the tail |
| JSONB audit payloads with UUIDs failed to serialise | Engine-level JSON serializer for UUID, Decimal and dates |
| Coverage under-reported async code (44–50% on services) | `concurrency = ["greenlet", "thread"]` → real figure 93% |
| Test suite ~3.5 min because each request opened a new connection (~150 ms on Docker Desktop) | Pooled engine in tests → ~70 s |
| Redundant index duplicating a unique constraint | Removed; indexes now declared in models so `alembic check` guards drift |
| `ARRAY.any()` can't use the GIN index | `categories @> ARRAY[...]` via `contains()` |
| Demo reset deleted departments before users | Explicit dependency-ordered teardown, asserted against the tenant table list |
| Image 613 MB (chown layer duplication) | Multi-stage build, no chown → 308 MB |

### Next stage: P4 public deployment
Needs decisions from the project owner (hosting platform, domain, budget). Proposed shape:
static SPA on a CDN; API container on a managed container platform; managed PostgreSQL
(the runtime role and RLS work unchanged, since FORCE RLS covers non-superuser owners);
migrations as a release step; nightly demo-tenant reset.

