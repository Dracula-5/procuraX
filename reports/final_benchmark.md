# ProcuraX benchmark report

**Status: limited synthetic smoke measurements only.** The sections below separate reproducible synthetic pipeline checks from real-data, model-comparison, workload and business measurements. Synthetic results are not production or real-world performance claims.

## Run metadata

| Field | Value |
|---|---|
| Dataset and version | `synthetic_procurement_text_v1`; `synthetic_spend_amounts_v1`; `synthetic_labeled_invoice_text_v1`; `synthetic_vendor_preferences_v1`; `synthetic_procurement_knowledge_v1`; `synthetic_rendered_invoices_v1`; real: `nta_qualified_invoice_qa` (NTA Q&A, 令和8年5月改訂) |
| Data rights / synthetic label | Generated deterministic synthetic fixtures, plus the NTA Q&A under 公共データ利用規約 1.0 (attributed, edited as described) |
| Hardware / cloud SKU | Local Windows host; CPU/RAM not recorded; no cloud SKU |
| Software and model versions | Python 3.12.7; Tesseract 5.5.3 (conda-forge) with `eng` traineddata; local TF-IDF/logistic SGD implementation; robust log-MAD baseline |
| Configuration and random seed | Classifier seed 23, 800/200 stratified split; anomaly seed 29, 5 categories, threshold 3.5; invoice parser seed 31, 100 records; rendered OCR seeds 53 (dev) / 54 (held-out), 40 invoices per condition; ranking seeds 41/42, 80/40 queries; retrieval 32 exact-term pairs |
| Git commit and evaluation date | Commit not recorded; 2026-10-01 |

## Capability results

| Category | Baseline | Candidate | Metrics to report | Result |
|---|---|---|---|---|
| Vendor ranking | Existing weighted quote score | Pairwise logistic SGD | NDCG@3, Precision/Recall@3, scoring latency | Synthetic only: NDCG@3 0.7125 vs 0.9745; Precision@3 0.5083 vs 0.6583; Recall@3 0.7625 vs 0.9875. Not real vendor-outcome evidence; see [synthetic run](vendor_ranking_synthetic.json). |
| Spend classification | TF-IDF + Logistic Regression | Embedding/transformer classifier | Accuracy, macro/weighted/per-class F1, confusion matrix, latency | Synthetic smoke only; see below. Real dataset and candidate comparison not run. |
| Invoice extraction | Tesseract + strict labeled-line parser | OCR-tolerant parser (measured); document AI / multimodal (not run) | Field accuracy, wrong-value rate, document exact match, latency | Held-out synthetic renders: clean 0.9725 → 0.9900, degraded scan 0.6225 → 0.8075, low-res 0.7100 → 0.7500 average field accuracy. See [Invoice OCR end to end](#invoice-ocr-end-to-end). Real documents not measured. |
| Anomaly detection | Robust category log-MAD amount baseline | Isolation Forest candidate | Precision, recall, F1, PR-AUC, false-positive rate | Synthetic injection smoke only; see below. Real history and candidate comparison not run. |
| Duplicate detection | Exact vendor/number rules | Similarity-assisted candidate | Precision, recall, F1, false-positive rate | Not run |
| RAG | BM25 with the previous ASCII tokenizer | BM25 with CJK bigrams (measured); hybrid + reranker (not run) | Recall@1/5, MRR@10, latency | **Real data:** Official NTA Q&A (171 questions, real data): Recall@5 0.158 → 0.906, MRR@10 0.138 → 0.793. See [Japanese retrieval](#japanese-retrieval-on-the-official-nta-qa). The older exact-term synthetic smoke (1.00) is superseded. |

## Engineering and workflow results

| Category | Configuration | Metrics | Result |
|---|---|---|---|
| API performance | Live free tier (Render Free, Oregon; Neon Free), public endpoints `/ready` + `/meta`, 30 s per run, from the developer workstation | Throughput, p50/p95, error rate | Concurrency 1: 30 requests, 1.0 req/s, p50 638 ms, p95 1722 ms, 100% HTTP 200. Concurrency 5: 148 requests, 4.79 req/s, p50 1434 ms, p95 1799 ms, 100% HTTP 200. Cold start 31 s. `/health` baseline TTFB median 478 ms, so most latency is network round trip plus TLS per request, not server work. See [live_deployment_smoke.json](live_deployment_smoke.json). Not a capacity estimate. |
| Database performance | Dataset size, indexes, connection pool to be recorded | Query latency, throughput, locks, pool saturation | Not run |
| Load testing | Local `/health`, 5 workers, 2 seconds, Windows/Python 3.12.7 | 612 responses; 304.63 req/s; p50 13.05 ms, p95 31.83 ms, p99 37.04 ms; 100% HTTP 200 | Health-only smoke. Authenticated product/API/database workload not run. |
| Workflow performance | Controlled sample and task timing to be recorded | Request decision time, invoice handling time, exception rate | Not run |
| Cost | Region, runtime, managed service SKUs and utilization to be recorded | Monthly fixed/variable cost and cost per completed workflow | Not run |

## Japanese retrieval on the official NTA Q&A

Reproduce from `backend/` with `uv run python -m app.scripts.benchmark_rag_nta_qa --output ../reports/rag_nta_invoice_qa.json`
(downloads the PDF; needs `pdftotext`). Raw output: [rag_nta_invoice_qa.json](rag_nta_invoice_qa.json).

- **Source (real, not synthetic):** 国税庁 (National Tax Agency, Japan), 「消費税の仕入税額控除制度における適格請求書等保存方式に関するＱ＆Ａ」 (令和8年5月改訂),
  SHA-256 `d0e50ed858d7ea7d…`. Used under 公共データ利用規約 第1.0版 (CC BY 4.0 compatible).
  出典：国税庁「消費税の仕入税額控除制度における適格請求書等保存方式に関するＱ＆Ａ」を加工して作成. Edits: Q&A pairs extracted from the PDF text layer, whitespace normalised, revision tags removed.
- **Task:** each of the 171 official questions must retrieve a chunk of its own official answer among all
  answers (299 chunks, production 900-character chunking). The NTA wrote both the questions and the
  answers, so the labels were not chosen by us.
- **Ranker:** the production BM25 function. The only difference between the rows is the tokenizer.

| Tokenizer | Questions with no terms | Recall@1 | Recall@5 | MRR@10 | Latency p50 / p95 ms |
|---|---|---|---|---|---|
| ASCII-word tokenizer (previous) | 100 | 0.1111 | 0.1579 | 0.1377 | 0.0 / 12.0 |
| CJK character bigrams (current) | 0 | 0.7135 | 0.9064 | 0.7935 | 78.6 / 110.0 |

The previous tokenizer kept only `[a-z0-9]` words, so Japanese text produced no terms at all. Its few hits come
from article numbers and Latin fragments. Character bigrams are the standard dictionary-free technique for CJK
text; they were chosen before this evaluation and not tuned on it.

The benchmark also exposed a chunking bug. The chunker never stopped at the end of a text and emitted about 120
near-duplicate tail fragments per document (20,785 chunks instead of 299). With that bug the
same bigram tokenizer scored Recall@5 0.731 at 2.45 s per query. The fix is in production code and covered by a test.

**Limits:** questions share vocabulary with their answers (in-domain lexical retrieval, not paraphrased user
questions); one relevant answer per question; retrieval only, with no generated answer and no human relevance
judgement. Latency is measured on the local workstation and re-tokenizes the corpus per query.

## Invoice OCR end to end

Reproduce from `backend/` with `uv run python -m app.scripts.benchmark_invoice_ocr --tesseract <tesseract.exe> --tessdata <tessdata dir> --output ../reports/invoice_ocr_synthetic.json`.
Raw output: [invoice_ocr_synthetic.json](invoice_ocr_synthetic.json).

- **Data:** `synthetic_rendered_invoices_v1`. Generated A4 invoices (150 dpi, one bundled font) with distractor
  addresses, a line-item table and notes; ten labelled fields including a check-digit-valid registration number.
  Each invoice is captured three ways: clean PNG, degraded scan (rotation ±1.5°, blur, speckle, JPEG q30) and
  half-resolution. Every fourth clean render is also wrapped in a PDF to exercise `pdftoppm`.
- **Pipeline:** the production adapter `extract_ocr_text` (tesseract 5.5.3, `eng`, `--psm 6`) then the parser in
  *strict* mode (the earlier baseline) or *OCR-tolerant* mode (punctuation clean-up on code fields, digit-confusion
  mapping in amounts, and a registration-number `T` repair accepted only when the corporate-number check digit
  validates).
- **Method:** the clean-up rules were written after inspecting errors on the dev seed (53).
  The held-out seed (54) was not inspected, so it is the comparison quoted below.
  Both parser modes score the same OCR text. Host: local Windows workstation, CPU/RAM not recorded.

Held-out seed, strict → OCR-tolerant:

| Capture condition | Docs | Avg field accuracy | Wrong-value rate | Docs fully correct | Registration no. accuracy | OCR latency p50 / p95 ms |
|---|---|---|---|---|---|---|
| Clean PNG | 40 | 0.9725 → 0.9900 | 2.75% → 1.00% | 75.0% → 90.0% | 0.825 → 1.000 | 401 / 534 |
| Degraded scan (JPEG) | 40 | 0.6225 → 0.8075 | 19.25% → 6.00% | 0.0% → 15.0% | 0.525 → 0.800 | 472 / 576 |
| Low resolution | 40 | 0.7100 → 0.7500 | 17.25% → 9.00% | 2.5% → 15.0% | 0.275 → 0.550 | 282 / 370 |
| Clean PDF | 10 | 0.9800 → 0.9900 | 2.00% → 1.00% | 80.0% → 90.0% | 0.900 → 1.000 | 1558 / 1673 |

Dev seed average field accuracy, for transparency: clean png 0.9675 → 0.9850, degraded scan (jpeg) 0.5625 → 0.7675, low resolution 0.7800 → 0.8250, clean pdf 0.9700 → 0.9900.

Wrong-value rate counts non-empty extracted values that differ from the label. Those are the risky errors
because a reviewer may accept them. An empty field is visible and gets keyed by hand. Remaining errors are
mostly lost or merged lines on degraded scans, digits misread at low resolution, and vendor names (`K.K.` read as `KK.`).

**What this does not show:** real supplier invoices, Japanese text, table extraction, stamps (hanko),
photographed or multi-page documents, confidence calibration, or a document-AI comparison. These are
synthetic renders with English labels, and the results are not production accuracy estimates.

## Failure analysis template

For each failure, preserve the input and version where lawful, expected outcome, actual outcome, root cause, business impact, mitigation, owner and retest result. Include incorrect vendor rankings, extraction errors, false anomaly alerts, duplicate false positives, unsupported RAG answers, unsafe query plans, policy errors and workflow/retry failures.

## Current engineering evidence

- 165 backend tests passed, with 92% line coverage for `app/` (`pytest --cov` against local PostgreSQL).
- Ruff, ruff format and mypy passed.
- Alembic upgraded an empty database to head, downgraded to base and upgraded again; `alembic check` reported no schema drift.
- Frontend `tsc -b` and Vite production build passed: 437.58 kB JavaScript (127.82 kB gzip), 23.59 kB CSS.
- Production-shaped container stack (migrate job, API with prod settings, nginx SPA) started from an empty database and served registration, authenticated reads and login throttling through the proxy.

## Synthetic baseline smoke results

Reproduce with `python -m app.scripts.benchmark_spend_classifier`,
`python -m app.scripts.benchmark_amount_anomaly` and `python -m app.scripts.benchmark_invoice_text`
`python -m app.scripts.benchmark_vendor_ranking` and
`python -m app.scripts.benchmark_rag_retrieval` from `backend/`. Spend, anomaly and invoice output is saved in
[synthetic_baseline_results.json](synthetic_baseline_results.json).

- **Spend classification:** 800 train / 200 test examples across 10 labels from category-word templates; TF-IDF + multinomial logistic regression (sparse SGD); accuracy 1.00 and macro-F1 1.00; local single-record latency p50 0.0229 ms, p95 0.0240 ms in the recorded run. Because train/test use the same synthetic vocabulary generator, these scores only show the pipeline runs end to end.
- **Spend anomaly:** 525 generated amounts across five synthetic categories, with 25 injected 12x outliers; category median + robust log-MAD threshold 3.5; precision 1.00, recall 1.00, F1 1.00, false-positive rate 0.00. The simple injected distribution makes this an implementation smoke check, not a realistic detection result.
- **Invoice text parser:** 100 synthetic `Field: Value` text samples; all nine fields and end-to-end text parsing matched the generated labels (1.00); average local parser time 0.1587 ms/invoice. The input is already clean extracted text; OCR, PDFs, images and table layout are not evaluated.
- **Vendor ranking:** 80 synthetic query groups train the pairwise ranker; 40 separate generated groups evaluate it against the current weighted score. The learned model improves ranking metrics on generated preference labels derived from a fixed hidden formula. This circular synthetic setup is for evaluator plumbing only; no historical sourcing decisions or supplier outcomes were used.
- **Knowledge retrieval:** 32 exact-term synthetic query/document pairs produced Recall@5, MRR@5 and top-1 citation correctness of 1.00. Queries repeat distinctive target words; real relevance and answer quality are not measured.
- **API liveness:** the load row above exercises only `/health`. No database or authenticated business operation is included.

These synthetic and engineering checks do not establish real-world model accuracy, product capacity, adoption or business value. The current delivery state is tracked in [transformation_roadmap.md](../docs/transformation_roadmap.md).
