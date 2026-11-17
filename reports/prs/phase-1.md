# Phase 1: missing screens and Japanese UI

Branch: `gaps/phase-1-screens-i18n` (stacked on `gaps/phase-0-docs`)

## What changed

### API
- **Per-user language.** Migration `0013_user_locale`: nullable `users.locale` with a CHECK generated from
  the new `Locale` enum (`en`, `ja`); NULL means "not chosen, follow the browser". `/auth/me` returns it;
  `PATCH /auth/me/preferences {locale}` sets or clears it. A display preference, so not audited.
- **Delegation screen endpoints.** Delegation moved from `procurement/router.py` to
  `procurement/delegation_router.py` (same paths, status codes and messages). New:
  `GET /approvals/delegable` (undecided steps named to the caller, never delegated) and
  `GET /approvals/{id}/delegate-candidates`. Candidates and creation use **one** eligibility query
  (active, holds the step's approver role, not the caller, not the requester), so the screen cannot offer
  someone the API would refuse. `GET /approvals/delegations` now includes request number/title, names,
  step status and `can_revoke`.
- **Reason codes.** Business-rule refusals the UI explains now carry `error.details.reason`
  (`delegation_*`, `zengin_invalid_characters|too_long|format`) and, for bank fields, `details.field`.
  `error.code` is unchanged, so existing clients are unaffected.
- **Bug fixed: payer consignor not validated on save.** `PUT /organizations/current/payment-settings`
  accepted a consignor name longer than the 40 characters the Zengin header allows (and any 10-digit
  code check was only at export). It now fails at save, naming the field, instead of breaking the next
  transfer-file export.
- **Payer settings readable by the org admin** who maintains them (previously write-only for that role;
  `requires_any(ORG_MANAGE, PAYMENT_APPROVE)`); still masked. Only the admin can change them.

### SPA
- **i18n:** i18next + react-i18next, `en` and `ja`. Keys are typed against the English messages
  (`i18next.d.ts`), and each Japanese namespace is typed `typeof` its English one, so a missing or extra key
  fails `tsc -b`. Japanese loads as its own chunk before first render (no English flash).
- **Every page and component translated**, including enums (statuses, roles, categories), rule titles and
  descriptions, audit actions, validation messages and API error codes. In Japanese, policy-rule messages
  are rebuilt from each hit's structured `evidence` (amounts, thresholds, budget overrun, split totals);
  English keeps the engine's exact sentence. Audit entries are described by their `action` code.
- **Locale formatting:** dates, numbers, percentages and ¥ amounts via `Intl` (ja-JP uses `￥` and
  `2026/04/01`); month names in the registration form come from `Intl`.
- **Language switcher** in the header and on the sign-in pages; saved to the user profile when signed in,
  to the device otherwise; a saved preference is applied on sign-in on any device.
- **New screens:** *Approvals → Delegations* (delegate, list given/received, revoke),
  *Vendors → Bank account* (`/app/vendors/:id/bank-account`) and *Payment settings*
  (`/app/admin/payment-settings`; admin edits, finance reads). Zengin character and length checks run
  inline while typing (a TypeScript mirror of `domain/zengin.py`, both tested against the same
  `zengin.cases.json`), with a preview of the half-width-kana form the bank receives.
- **Route-level code splitting** for signed-in pages. Main bundle 437.6 kB → 298.1 kB (gzip 127.8 → 93.2 kB)
  even with i18next added; the Japanese strings are a separate 44.9 kB chunk.
- **Fixed:** the Purchase-to-pay page passed error *strings* to `ErrorNotice`, which only reads `Error`
  objects, so users saw "Something went wrong" instead of the reason. The landing page's "What is built
  today" listed PO/receipt/invoice/3-way match as roadmap; it is built (partially) and now says so.
  Spend CSV export starts with a UTF-8 BOM so Excel (Japanese Windows) opens it correctly.
- **Vitest + Testing Library** (`npm test`, added to CI).

### Docs
ADR-012 (API returns codes, SPA owns wording), product spec (delegation ✅, escalation row corrected,
UX row), interview guide, README (12 ADRs, `npm test`), roadmap.

## How it was tested
- Backend: `tests/api/test_phase1_screens.py` (language preference; delegable steps and candidates
  follow the rules; each refusal's reason code; list names and revocability; tenant scoping; bank and
  payer field errors; consignor checked at save; payer settings readable by admin and finance only) and
  `tests/unit/test_zengin_fields.py` (shared cases, field/kind on errors, consignor limits).
- Frontend (Vitest): translations have identical keys and placeholders; no empty or untranslated
  Japanese strings; the UI's rule labels equal the generated rule catalogue; the Zengin mirror matches the
  shared cases; formatting per language; error mapping (reason → field-aware message, code fallback,
  network); rule-message rebuilding; permission guard; language switcher (saves when signed in, device
  only when not); bank-account form (inline problems, preview, save, server refusal next to the field in
  Japanese); delegation screen (candidates, delegate, revoke only where allowed).
- Full check list: see `reports/gap_closure.md` (Phase 1 row) for the run.

## Still open
- **Japanese wording has not been reviewed** by a native-speaking procurement user; terms follow common
  usage (購買申請, 稟議, 検収, 振込依頼人, 委託者コード) but need that review before a Japanese pilot.
- **Server-generated free text stays English** in the Japanese UI: quote-ranking reasons, invoice match
  exception sentences, assistant answers and evidence labels, OCR field names, decision-support
  interpretation and stored audit summaries. Translating them needs structured reason codes from those
  backends (a follow-up per module), not translation of stored sentences.
- `Field` puts hint text inside the `<label>`, so an input's accessible name includes its hint
  (pre-existing; tests match labels by substring). A proper fix uses `aria-describedby`.
- Delegation is per step, not a date-range out-of-office rule; Playwright e2e is Phase 9.
