# Phase 3: invoice file storage

Branch: `gaps/phase-3-invoice-storage` (stacked on `gaps/phase-2-notifications`)

## What changed
- **Storage interface** (`app/core/storage.py`): `LocalStorage` (development) and `S3Storage` (any
  S3-compatible service: RustFS locally/CI, AWS S3 or Cloudflare R2 in production), selected by
  `PROCURAX_STORAGE_BACKEND`. Write-once: local `O_EXCL`, S3 `If-None-Match: *`. Keys are
  `<org>/invoices/<yyyy>/<mm>/<uuid>`, never the uploaded name; traversal-safe. Production refuses
  uploads to local disk (503 `storage_not_configured`) instead of losing evidence on an ephemeral disk.
- **Pure rules** (`app/domain/documents.py`): the existing type, magic-byte and 18 MiB limits, now also
  enforced at storage with a real PDF page count (`pypdf`, 1–10 pages, encrypted PDFs refused);
  SHA-256 identity; retain-until date; when attached files block payment. The OCR adapter now imports the
  same limits.
- **`invoice_documents`** (migration 0015): object key, SHA-256, size, MIME type, page count, uploader,
  retain-until, scan status/detail/attempts, invoice link. Forced RLS (0001 predicate), runtime role
  SELECT/INSERT/UPDATE only, and a trigger that rejects any change to a stored file's identity, any
  re-link, and deleting a linked row, also for the owner role (except under a transaction-local purge
  flag that only the fictional demo reset sets).
- **API**: `POST /invoice-documents` (base64 upload, procurement), `POST /invoices/{id}/documents` (link
  once; infected refused), `GET /invoice-documents` and `GET /invoice-documents/{id}/download`
  (`purchase_request:read_all` or `payment:approve`): 60-second SigV4 presigned URL (307 or
  `?mode=link` JSON) or streamed with a SHA-256 re-check, `Content-Disposition` with an RFC 5987 UTF-8
  name, `nosniff`, `no-store`. Infected and still-scanning files cannot be downloaded.
- **Scan via the worker**: clamd INSTREAM client (no dependency); the worker cycle now runs
  notifications and scans (`app/background.py`). Scans re-hash the stored object. Outages leave files
  pending with backoff; nothing is marked clean without a scan; with scanning off files are
  `not_scanned`.
- **Payment gate**: requesting or approving payment for an invoice with an attached infected, pending
  or (unless `PROCURAX_ALLOW_UNSCANNED_DOCUMENTS`) not-scanned file is refused with a reason code.
  Invoices without files behave as before.
- **Retention**: `PROCURAX_DOCUMENT_RETENTION_YEARS` (default 7) recorded per file; documented with
  bucket versioning/object lock as the production enforcement. Nothing is auto-deleted.
- **SPA**: an *Invoice files* card on Purchase to pay (attach, scan status, SHA-256, retain-until,
  download), translated; new error reasons translated.
- **Infra**: RustFS in `docker-compose.yml` and the production-shaped stack (with a `storage-init` job),
  optional ClamAV compose profile, RustFS service container in CI, `.env.example`, deployment and
  local-verification guides, ADR-014.

## Substitution: MinIO → RustFS
The brief asked for MinIO. Its community container images are no longer published (`minio/minio` is
gone from Docker Hub; `quay.io/minio/minio` and `bitnami/minio` are not pullable). RustFS 1.0.5
(Apache-2.0, MinIO-compatible API and keys) and SeaweedFS were both checked for the two behaviours the
code relies on, conditional writes and presigned GETs; RustFS was chosen because it runs as a GitHub
Actions service container without a custom command.

## Found and fixed
- boto3 signed presigned URLs with legacy SigV2 against a custom endpoint; AWS rejects SigV2 in newer
  regions. Forced SigV4 (the RustFS test asserts `X-Amz-Signature`).

## How it was tested
- Unit (`tests/unit/test_documents_domain.py`): accepted types and facts; empty, too large, bad
  extension, signature mismatch, unreadable, encrypted, too many pages; retention (29 Feb); payment-gate
  matrix; clamd reply parsing; local storage write-once and traversal; **S3 storage against RustFS**
  (write-once, SigV4 presigned GET with UTF-8 filename).
- Integration (`tests/api/test_invoice_documents.py`): upload/link-once/download with headers;
  permission and tenant checks; refusals with reasons; production local-disk refusal; payment blocked by
  not-scanned unless allowed; scan via the worker against a clamd protocol stand-in (clean and infected,
  link refused, download blocked, payment allowed after clean); a file linked after the request blocks
  approval; scanner outage keeps pending with backoff; tampered stored object caught by the hash check;
  trigger blocks identity changes, re-link and delete even for the owner; runtime role cannot delete;
  RLS (scoped, fail-closed, cross-tenant insert rejected).
- **Real ClamAV 1.4**: clean PDF → clean (directly and through upload → worker); raw EICAR detected.
  EICAR inside a PDF is not detected (EICAR must start the file), so the infected API path relies on the
  stand-in.

## Still open
- Not run against AWS S3 or Cloudflare R2 (needs your account and keys).
- No automatic deletion at end of retention; uploads go through the API (18 MiB cap), not presigned
  direct uploads; an orphaned object can remain if a commit fails after the upload.
