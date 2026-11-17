# Phase 8: AI gateway, advisory flags, retrieval, cited answers, SQL guard, Japanese OCR

Branch: `gaps/phase-8-ai` (stacked on `gaps/phase-7-observability`)

## What changed

### Gateway and boundaries (ADR-024)
- `app/ai/gateway.py`: the only path to models. Every output goes to `ai_predictions` (migration 0023; tenant
  RLS; runtime role SELECT/INSERT only) with model, version, input reference, input SHA-256, latency and
  tokens.
- LLM providers behind `PROCURAX_LLM_PROVIDER`: `replay` (default, recordings keyed by prompt hash, no
  network) and `anthropic` (official `anthropic` SDK 2.8.0 via `uv add`; `claude-opus-5-5`; server-side refusal
  fallback `fallbacks: "default"`; cached system prompt; refusals, rate limits and outages degrade to the
  deterministic path; optional recording for replay). **The real API was not called.**
- `tests/unit/test_ai_boundaries.py`: fails if `app/domain/policy` (and the rest of `app/domain`, the
  workflow, matching, Zengin, payment export or journal export) imports `app.ml`, `app.ai` or `anthropic`, or if
  `app/ai` assigns a status/decision column.

### Advisory flags
- `app/ai/advisors.py`, run after the policy decision on submission (savepoint; failures logged, never
  blocking): `amount_outlier` (robust log-MAD within the category, last year, ≥ 8 observations) and
  `possible_duplicate` (same requester and vendor, title Jaccard ≥ 0.6, last 30 days).
  `GET /purchase-requests/{id}/advisories` with the request's own visibility; "Advisory flags" card on the
  request page ("does not change the approval route").

### Retrieval and cited answers (ADR-025)
- `app/ai/text.py` (tokenizer, BM25, chunking moved out of the router), `app/ai/embeddings.py` (deterministic
  feature-hashing embedding, 256 dims), `app/ai/retrieval.py` (BM25, or BM25 + pgvector cosine fused by RRF).
- Migration 0024: `CREATE EXTENSION vector`, `knowledge_chunks` (`vector(256)`, HNSW cosine index, tenant RLS);
  documents indexed on upload and lazily for older ones.
- Knowledge answers: rankings of documents and policy rules are fused by rank (a scale bug — BM25 scores mixed
  with RRF scores — was found by a test and fixed); a model summary is shown only if every `[n]` cites a
  given source; otherwise the extractive citations alone. "AI summary" badge in the UI.
- **Default `PROCURAX_RETRIEVAL_MODE=bm25`**, because hybrid with the local embedding measured lower (below).

### Guarded SQL (ADR-026)
- `app/ai/sql_guard.py` (sqlglot 30.31): one SELECT; allow-listed tables, columns and functions; no `*`, INTO,
  locks, VALUES, comments, schema-qualified names; LIMIT ≤ 200 enforced.
- `POST /procurement-assistant/sql-query` (`analytics:read`): model draft → guard → read-only, 3-second
  savepoint under RLS → rows; every draft/refusal recorded. "Ask with SQL" panel in the assistant.

### Japanese OCR
- `app/ml/invoice_text.py`: Japanese labels (取引先, 請求書番号, 登録番号, 請求日, 小計, 消費税, 合計, …), NFKC
  (full-width digits/colons), 円 amounts, 和文 dates; OCR-tolerant mode also accepts a missing colon after a
  Japanese label and a dot as the thousands separator inside a 円 amount.
- `app/scripts/benchmark_invoice_ocr_ja.py`: synthetic Japanese invoices rendered on the host, Tesseract inside
  the production API image, scored by the production parser (field accuracy, wrong-value rate, documents
  fully correct, OCR latency); seed 61 development, seed 62 held-out.

## How it was tested
- Unit (43 incl. existing ML tests): 23 SQL-guard cases (accepted analytics/CTE/join queries; DELETE, UPDATE,
  stacked statements, `set_config`, `current_setting`, `pg_sleep`, bank details, users/organizations tables,
  `pg_catalog`, `*`, INTO, FOR UPDATE, comments, data-modifying CTE, COPY, schema-qualified, empty, unparsable),
  embeddings, RRF, Japanese BM25, replay hits/misses, Japanese invoice parsing, the four boundary tests.
- Integration (4 new): knowledge documents chunked with 256-dim vectors; BM25 default and hybrid mode; a
  correctly cited model answer shown and recorded, a mis-cited one hidden and recorded as not accepted; SQL
  answered under RLS (org A sees its 1 request, not B's 3), `set_config` refused, a DELETE injected past the
  guard refused by the read-only transaction (row still there), UNANSWERABLE, replay miss, 403 without
  `analytics:read`, all drafts recorded; advisory flags raised (outlier + duplicate) with the same policy
  result, route and rule hits when the advisors fail; other tenants get 404; RLS on both new tables;
  `ai_predictions` UPDATE/DELETE denied.
- Vitest (3): advisory flags card, empty state, SQL panel (rows, guarded SQL, refusal reason).
- **Measured (real runs on this workstation):**
  - NTA Q&A retrieval (official, 171 questions): Recall@5 — BM25 bigrams 0.906, hashing vector 0.719, hybrid
    RRF 0.825; MRR@10 0.794 / 0.540 / 0.674. Hybrid lost, so BM25 stays default.
  - Japanese invoice OCR (synthetic, held-out seed 62, 30 per condition, tesseract 5.5.0 `jpn+eng`): average
    field accuracy strict → OCR-tolerant: clean 0.790 → 0.987, degraded 0.657 → 0.950; wrong-value rate clean
    0.100 → 0.007, degraded 0.217 → 0.030; `eng` only 0.000.

## Still open
- **The Anthropic API was not called** (needs your API key; every call costs money). No recordings were made,
  so replay mode currently answers nothing and the deterministic paths are what users see.
- No semantic embedding model; the hybrid path is built and measured but not the default.
- Japanese OCR measured on synthetic renders only; no real scanned invoices, no document-AI comparison.
- SQL runs as the runtime role inside a read-only savepoint; a dedicated read-only role with views is the next
  hardening step if the feature is used. No acceptance/override analytics on `ai_predictions` yet.
