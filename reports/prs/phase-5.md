# Phase 5: contracts, multi-currency, ERP journal export

Branch: `gaps/phase-5-contracts-fx-erp` (stacked on `gaps/phase-4-japan-compliance`)

## What changed

### Contracts (ADR-017)
- Pure domain `app/domain/contracts.py`: states draft → active → expired / terminated, `transition()`,
  term validation, which in-force contract covers a vendor + category + date (latest ending wins), the
  vendor's contract status from records (or `None` → use the old `vendors.contract_status` field), expiry
  and a single 30-day reminder.
- Migration 0017: `contracts` (tenant RLS, runtime role SELECT/INSERT/UPDATE) and
  `purchase_requests.contract_id`.
- API `/contracts`: list/get (vendor:read), create/edit draft, activate, terminate with a reason
  (vendor:manage). Status changes lock the row and go through the state machine; every change is audited.
  Signed file: same type/size/page checks as invoice files, write-once key, SHA-256, scanned synchronously
  when ClamAV is on (infected refused, scanner down → 503), fixed once the contract is active; download
  after a permission check (presigned link or streamed with hash re-check).
- Policy: ENGINE_VERSION 1.1. VEN-004 now reads the contract records; new **CON-001** (route to
  procurement) when committed spend under the covering contract plus this request exceeds the cap, or when
  the cap's currency has no rate. `route_contract_cap_overrun` in the policy config (default on).
- Worker cycle: contract sweep (`FOR UPDATE SKIP LOCKED`, explicit RLS bypass) expires active contracts
  after their end date (audited, actor `system`) and queues one `contract.expiring` notification 30 days
  before (en/ja templates; procurement officers and the contract's creator).

### Multi-currency (ADR-016)
- Migration 0018: `exchange_rates` (tenant RLS; runtime role SELECT/INSERT/UPDATE) and the request
  snapshot columns `fx_rate`, `fx_rate_on`, `fx_source`, `base_amount` (backfilled from `estimated_total`
  under the explicit bypass, since the owner may not be a superuser).
- `app/domain/fx.py`: rate choice (latest on or before the date; manual beats ECB on the same day),
  half-up conversion to the base currency's minor unit, ECB cross rates.
- Requests take a `currency` (13 supported ISO codes). At submission the rate is snapshotted and
  `base_amount` stored; policy, the split-purchase window, budget committed/pending, the final budget
  recheck (now formatted in the budget's currency), contract caps, analytics and the assistant's
  thresholds use `base_amount`. **VAL-004** blocks only when no rate exists.
- `/fx/rates`: read by any signed-in user; manual rate upsert by budget:manage or organization:manage
  (no future dates, not the base currency, audited); `POST /fx/rates/refresh` fetches ECB reference rates
  only when `PROCURAX_FX_ECB_ENABLED` (off by default; `app/integrations/ecb.py`, defusedxml).

### ERP journal export (ADR-018)
- Pure `app/domain/journal.py`: control accounts, expense account by mapping (category + cost centre >
  cost centre > category > default), invoice entries (expense, input tax / payables), payment entries
  (payables / bank), every entry balanced, CSV (CSV-injection safe) and JSON rendering.
- Migration 0019: `organizations.ledger_settings`, `gl_account_mappings` (RLS; full CRUD for
  configuration), `journal_exports` (RLS; **SELECT/INSERT only**; file + SHA-256 kept),
  `invoices` / `payments.journal_export_id`.
- `/ledger/settings`, `/ledger/mappings` (organization:manage writes; payment:approve reads),
  `/journal-exports/pending`, `POST /journal-exports` (payment:approve): locks the organisation row and
  the included documents, exports matched / exception-approved invoices and payments whose bank file was
  produced, at most 2,000 entries per batch, marks them, audits, returns the file;
  `GET /journal-exports` and `/{id}/download` (hash re-checked).

### SPA
- New screens: **Contracts**, **Exchange rates**, **Journal export** (nav, routes with permission
  guards); currency choice on the request form; request detail shows the base amount, the rate used and
  the covering contract; policy evidence is formatted in the base currency.
- CON-001 and the new VAL-004 wording, audit actions and the `contract.expiring` notification in English
  and Japanese; the rule-ID test still matches the generated rule catalogue.
- `lib/files.ts` shares upload/download helpers with the invoice files card.

### Docs
- ADR-016, ADR-017, ADR-018; system design (integration table, data architecture, ERD); README
  (18 ADRs, 22 rules, new rows); deployment guide (exchange rates, journal export); business problem
  assumption; interview guide; roadmap; `.env.example`; generated policy reference / OpenAPI.

## How it was tested
- Unit (20 new): contract transitions, term validation, coverage (vendor, category, dates, drafts,
  overlapping contracts), status from records vs legacy, reminder window and once-only, expiry boundary;
  rate choice and manual precedence, rounding, cross rates; CON-001 within / over / no cap / no rate /
  switched off; VAL-004 with and without a rate; account mapping specificity; journal entries balanced, in
  a stable order, with and without tax; unequal invoice totals refused; CSV header and formula escaping;
  JSON shape; ECB parsing of a fixture (bad rate skipped) and refusal of garbage, billion-laughs and XXE.
- Integration (9 new): contract lifecycle drives VEN-004/CON-001 end to end (draft ignored, active covers,
  cap overrun routes procurement, terminated → "expired"), permissions, edit locked after activation;
  sweep reminds once then expires; signed file upload, type refusal, lock after activation, download;
  foreign-currency request blocked without a rate, converted with one (¥150,250 → manager tier), rate
  correction does not change the snapshot, budget pending in base currency; ECB refresh (fetch replaced
  by the fixture) records cross rates for supported currencies only; journal export balanced with
  mapping, tax line and payment entry, second export refused, history and byte-identical download; JSON
  format; RLS as the runtime role for all four new tables; `journal_exports` UPDATE/DELETE denied.
- Vitest (6 new): contracts list/activate, read-only without vendor:manage, currency choices; manual
  rate entry and the ECB-off notice; journal export blocked until configured and a refused export shown.
- Existing suites unchanged in behaviour: all earlier API tests pass with the engine and FX changes.

## Still open
- The ECB adapter was exercised with a fixture in the ECB file format, **not fetched live**; it is off
  by default and needs no credentials.
- The journal is a file for a person to import; no ERP API push, no PO / vendor master sync.
- Contract files are scanned at upload only when ClamAV is on; they are not re-scanned by the worker.
- No date-effective contract amendments (a draft can be edited; an active contract is terminated and
  replaced).
