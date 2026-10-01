# Accenture Japan Digital Consultant interview guide — ProcuraX

Use this as a discussion guide, not a script. The client case is fictional. Separate implemented controls from planned AI and unmeasured business outcomes.

## Business

**What problem does this solve?** Procurement work spread across mail, spreadsheets, ERP exports and approval documents makes policy inconsistent, delays decisions and weakens spend visibility and audit evidence. ProcuraX provides one governed request-to-approval workflow and a data foundation for downstream P2P.

**Who are the users?** Requesters, managers, department heads, procurement officers, finance analysts/managers, vendors, organization administrators, platform operators and read-only analysts. Access is role based and tenant scoped.

**Why transform procurement?** Procurement is both a control point and a source of operating data. Digitization can move policy checks earlier, reduce handoffs, expose exceptions and give finance a timely view of commitments. The case for investment must be established from the client’s baseline.

**What is the value proposition and which KPIs may improve?** Faster request decisions, higher first-pass invoice matching, better contract coverage and shorter audit evidence retrieval are hypotheses. Measure cycle-time median/p90, touch time, policy exceptions, spend coverage, match rate and rework before making claims.

**How would an enterprise justify investment?** Establish addressable spend and process cost, quantify a baseline, identify control and integration costs, estimate benefit ranges with Finance, run a limited pilot and compare against a control group or credible historical baseline. Include change, support and licensing costs.

## Consulting

**Why this problem?** Procurement links business demand, policy, suppliers, finance and audit; it demonstrates process transformation and system design without pretending AI is the whole solution.

**How would you diagnose it?** Map the process, interview each persona, observe real transactions, sample policies and invoices, reconcile ERP/AP extracts, identify rework and handoffs, then validate root causes with process owners.

**What alternatives were considered?** Workflow-only automation; AI added to existing tools; or an integrated workflow and intelligence platform. ProcuraX recommends staged workflow-first adoption because it creates reliable inputs and control evidence before AI.

**Why this architecture?** A modular monolith keeps transaction rules and tenant boundaries easy to reason about at this scale, while domain modules define future service seams. PostgreSQL provides relational integrity and row-level tenant controls. A queue and separate workers are future needs for OCR and long-running model work, not currently implemented.

**Where does AI help, and where should it not be used?** Extraction from varied invoices, vendor ranking, spend categorization and anomaly triage can benefit from ML after evaluation. Deterministic policy thresholds, authorization, budget locking, duplicate hard blocks and final financial approvals should stay deterministic or human-controlled.

**How would you present to a CIO/CFO?** Lead with baseline pain and controls, quantify what is known, show a staged target operating model, explain integration and risk, then seek agreement on a bounded pilot and success measures.

**What first with a limited budget?** Digitize one high-volume category and department, enforce clear approval rules, capture timestamps and exceptions, and measure cycle time and rework before buying advanced AI.

## Technology

**Why PostgreSQL?** Transactions, constraints, JSONB for explainability snapshots, mature indexing, and row-level security in one relational store.

**Why Redis?** It is not in the current implementation. Consider it only if measured caching, distributed rate limiting or queue needs justify operating another stateful service.

**Why event-driven processing?** Invoice extraction and notifications may be slow, retryable work and should not hold an API request open. A durable queue with idempotent workers is planned; it is not yet built.

**Why not microservices?** Current functional scope benefits more from transactional simplicity and a single deployment unit. Split only when team ownership, independent scaling, fault isolation or deployment cadence makes the operational cost worthwhile.

**How scale?** Profile and load test first; add stateless API replicas, connection pooling, queue workers for asynchronous work, indexed queries and a read/analytics path. Partition or introduce warehouses only when observed volume warrants it.

**What if ERP is unavailable?** Queue outbound integration with idempotency keys, retries and dead-letter review; show users the pending state. Reconcile after recovery and never report an external write as complete before acknowledgement.

**How handle retries/idempotency?** Persist a client or event idempotency key with a unique constraint, make handlers safe to replay, use bounded exponential backoff, and route permanent failures to an operator queue.

## AI

**Why ML for vendor ranking? Why not an LLM?** Structured historical price, delivery, defects and contract outcomes are rankable numeric evidence. A ranking model can be evaluated against historical decisions and explained with features; an LLM should not be the source of policy decisions.

**Why RAG?** Procurement policies and guidelines change. Retrieval can cite current, permission-filtered source text instead of relying on model memory. ProcuraX supports tenant-owned, versioned plain-text sources and BM25 extractive citations alongside active policy lookup. Embeddings, reranking and human-judged corpus evaluation remain future work; the perfect-score smoke fixture repeats query terms by design.

**How detect hallucinations?** Require citations, test answerability and citation correctness, compare extracted claims with source spans, log low-confidence cases and fall back to “not found” or human review.

**How evaluate invoice extraction?** Use a licensed, representative, labeled set; split by supplier/document family to reduce leakage; report field-level precision/recall/F1, numeric accuracy, table accuracy, end-to-end exact match and latency. Measure OCR and advanced model against the same baseline.

**How detect drift and low confidence?** Track input quality, feature distributions, calibration, error rates from adjudicated samples and subgroup performance. Route low-confidence results to human review and retrain only after controlled evaluation.

## System design prompts

**1M transactions/day:** About 11.6 transactions/s average before peak factors. Clarify transaction mix and latency/SLO, keep APIs stateless, use a managed PostgreSQL cluster with tested partitioning/index strategy, durable event queue, horizontally scaled idempotent workers, tenant-aware rate limits, object storage for documents, and a warehouse for large analytics. Load test peaks and failure modes; do not infer capacity from this demo.

**Multi-region:** Define RPO/RTO and residency first. Use regional stateless services, managed database replication/failover with tested recovery, region-local document storage, queue replay and tenant routing. Avoid active-active writes until conflict semantics and policy data ownership are designed.

**Tenant isolation:** Bind tenant identity from a verified token, enforce org predicates in service queries, use forced database RLS and a least-privilege runtime role, test negative cross-tenant read/write cases, and keep platform operations separate from tenant business data.

**Invoice pipeline:** Authenticated upload → malware/type/size checks → encrypted object storage → queue event → OCR/layout extraction → schema and arithmetic validation → PO/receipt match → deterministic duplicate check → review queue → audit and ERP hand-off. Preserve source file hash and extraction provenance.

**Approval workflow:** Evaluate versioned deterministic policies at submission; persist required roles, reasons and due dates; enforce segregation of duties; lock the request and relevant budget at final approval; record every transition atomically with audit evidence.

**Analytics at scale:** Keep transactional dashboards on bounded indexed aggregates initially; emit change events or batch CDC to a warehouse for historical cohort analysis; reconcile warehouse totals to the system of record.

## Case interview frameworks

### “Reduce procurement costs by 15%”

Clarify cost baseline and scope → segment direct/indirect spend and categories → separate price, demand, process and leakage drivers → assess contracts, supplier consolidation, specifications and compliance → quantify addressable opportunities and risks → prioritize pilots → establish Finance-owned baseline and savings tracking. Treat 15% as the client’s target, not a promised outcome.

### “Digitize procurement across 10 countries”

Map country process and regulatory differences → identify global standards vs local exceptions → inventory ERP, identity, vendor master and data-residency needs → define global data model and localization → choose a pilot country/category → integrate and test controls → train change champions → sequence rollout with adoption and service metrics.

### “Introduce AI to procurement”

Start with a business decision and baseline → assess data rights, quality and process consistency → rank use cases by value, risk and feasibility → begin with bounded advisory extraction/classification → evaluate against human labels and a simple baseline → keep approval and policy deterministic → monitor errors, drift, overrides and realized value before scaling.

## Resume evidence guardrail

Safe to discuss now: implemented tenant-scoped request workflow, policy engine, RBAC/RLS design, supplier line-price PO pricing, the cumulative three-way match (and the double-billing defect it fixed), consumption-tax and qualified-invoice checks, the recommendation-vs-decision-vs-outcome record, advisory-locked SLA escalation, the synthetic OCR benchmark with its held-out method, and the locally verified container stack. Do not claim public deployment, real pilot users, AI accuracy, ROI, cost savings or load capacity until those are measured and documented.

### Resume bullet drafts

- Implemented a multi-tenant procurement application with organization-scoped APIs, role-based permissions and PostgreSQL forced row-level security; added API and database tests for cross-tenant isolation.
- Built a deterministic, versioned procurement policy engine with sequential approval routing, budget rechecks under row locks, segregation-of-duties controls and an append-only audit trail.
- Extended approved requests through audited quote award, supplier line-price PO pricing, a cumulative line-level three-way match serialized by row locks (blocking double billing), Japan consumption-tax and qualified-invoice registration checks, finance exception review and payment approval requests; payment execution remains external scope.
- Recorded each sourcing award against the baseline recommendation shown, required override reasons, and reported delivery and invoice outcomes for followed vs. overridden awards (human-in-the-loop evidence).
- Added live tenant-scoped spend summaries by category, department, vendor and invoice match status, including 12-month trend and median/P90 decision cycles, with a React purchase-to-pay operations view.
- Made the assistant's cited retrieval work on Japanese text (NFKC + character bigrams) and measured it on the National Tax Agency's official invoice-system Q&A: Recall@5 rose from 0.16 to 0.91 across 171 official questions. The evaluation also exposed and fixed a chunking bug that produced about 120 duplicate fragments per document.
- Added Zengin (全銀) 総合振込 bank transfer-file export for approved payments, with kana validation, row-locked once-only export and audited file hashes.
- Prepared a zero-cost deployment (Render API, Netlify web app, Neon database, GitHub Actions: release migrations, scheduled SLA sweep, nightly demo reset) with a public-demo mode, and rehearsed it against a database whose owner is not a superuser, which surfaced two RLS-bypass defects.
- Added advisory-locked scheduled SLA escalation, versioned tenant knowledge ingestion with cited BM25 retrieval, and Tesseract invoice OCR. A check-digit-gated clean-up step raised held-out synthetic field accuracy on degraded scans from 0.62 to 0.81 and cut the wrong-value rate from 19.25% to 6.00%.
- Added regression coverage for delegation, tenant-scoped P2P, concurrent invoicing, tax and registration rules, override recording and knowledge isolation; the backend suite reports 165 passing tests and 92% line coverage. Fixed migrations so an empty database installs and round-trips cleanly.
- Built a typed React SPA (production Vite build 437.58 kB JavaScript, 127.82 kB gzip) and a production-shaped container stack (migration job, API, nginx) verified locally.

These describe implementation and local verification only. They do not claim enterprise transaction scale, production deployment, AI performance, adoption or realized business benefits.
