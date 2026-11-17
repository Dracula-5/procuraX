# Phase 4: Japan compliance (NTA registry, Peppol / JP PINT import)

Branch: `gaps/phase-4-japan-compliance` (stacked on `gaps/phase-3-invoice-storage`)

## What changed

### NTA qualified-invoice registry lookup
- Pure rules in `app/domain/qualified_invoice.py`: `RegistryRecord` and `registry_status(record, on)` —
  active, not_found, not_yet_registered, revoked or expired **on the invoice date**.
- `evaluate_registration` / `match_invoice` take optional registry evidence. The match records
  `qualified_invoice.registry` (source, status, issuer name, dates, fetch time). An issuer that was not
  active on the invoice date turns the invoice into a mismatch for finance review; without evidence the
  previous checks (check digit + vendor master) decide, as before.
- Adapter `app/integrations/nta_registry.py`: the official Web-API (v1 `num`, JSON) only, never the
  website; `PROCURAX_NTA_REGISTRY_ENABLED` (off) and `PROCURAX_NTA_APPLICATION_ID`; production refuses the
  flag without an ID. Lookup order: fresh cache → API → stale cache (`cache_stale`) → none
  (`unavailable`). Runs before the purchase-order row lock is taken.
- Migration 0016: `qualified_invoice_registry_cache` (public registry data, shared across tenants, so no
  org_id/RLS; runtime role SELECT/INSERT/UPDATE).
- `tests/nta_mock.py`: a real-HTTP mock of the Web-API serving fixture responses (active, revoked,
  not found, outage) and checking the application ID.

### Peppol / JP PINT UBL import
- `app/domain/einvoice.py`: parses UBL 2.1 Invoice (Peppol BIS / JP PINT) with `defusedxml` (DTDs,
  entities and external references refused; 5 MiB cap) and maps lines to PO lines by
  `OrderLineReference/LineID` or in order. Mapping table in `docs/einvoicing.md`.
- `POST /api/v1/einvoices`: finds the PO by `OrderReference` (or an explicit id), checks currency, builds
  the normal invoice input and runs **the same** `_create_invoice` path (shared with manual entry), so
  price, cumulative quantity, per-rate consumption tax, registration (incl. registry) and duplicate checks
  all apply. The match records the source (`peppol_ubl`, CustomizationID, JP PINT flag, supplier name,
  XML SHA-256).
- Fixtures: `tests/fixtures/einvoice/jp_pint_invoice.xml` (10 % and 8 % lines), billion-laughs and
  external-entity samples; `tests/fixtures/nta/*.json`.
- SPA: an e-invoice import card on Purchase to pay; registry status shown next to the registration check;
  new reason codes translated.
- Docs: `docs/einvoicing.md` (including what a Peppol access-point integration would need), ADR-015,
  system design, deployment, README, roadmap, product spec, `.env.example`.

## How it was tested
- Unit: announcement parsing (active, revoked latest record, not found, bad shape); registry status on the
  invoice date (6 cases); registry evidence turning a match into an exception, and the fallback record;
  JP PINT sample parsing (fields, tax 3,800, rates 0.10/0.08); BaseQuantity pricing; unsafe/unsupported
  documents (billion laughs, XXE, not XML, credit note, empty); line mapping by reference, by position, and
  a bad reference; production guard for the application ID.
- Integration against the mock: first lookup from the API with the application ID sent, second from cache
  with no extra call; revoked and unregistered numbers become exceptions; outage falls back to local checks
  (`unavailable`) or to a stale cache (`cache_stale`); disabled keeps the previous behaviour.
- Integration for UBL: JP PINT invoice imports and matches (tax per rate), duplicate import refused;
  price difference → mismatch; currency, unknown order, bad line reference, unsafe XML and bad encoding
  refused with reason codes; another tenant cannot target the order; employee forbidden.

## Still open
- **The real NTA Web-API was not called** — it needs your application ID. Request parameters and field
  names follow the published v1 specification and must be confirmed with a live call.
- Full JP PINT / Peppol BIS Schematron validation, and sending over Peppol (needs a certified access
  point) are out of scope. The received XML is hashed, not stored as a file.
