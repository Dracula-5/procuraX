# E-invoicing (Peppol / JP PINT) and the NTA registry

## What is built

**Receiving UBL invoices.** `POST /api/v1/einvoices` accepts a UBL 2.1 `Invoice` document (Peppol BIS
Billing 3 or JP PINT, `urn:peppol:pint:billing-1@jp-1`) as base64. The document's
`cac:OrderReference/cbc:ID` finds the purchase order (or `purchase_order_id` is given explicitly), the
lines are mapped onto PO lines, and the invoice then goes through exactly the same checks as a manually
entered one: per-line price, cumulative billable quantity, consumption tax per rate (10 % / 8 %),
qualified-invoice registration number (check digit, vendor master and, when enabled, the NTA registry),
and the duplicate guard. The match evidence records the source (`peppol_ubl`, CustomizationID, whether it
is JP PINT, the supplier name and the SHA-256 of the XML).

| Invoice field | UBL path |
|---|---|
| Invoice number | `cbc:ID` |
| Invoice date | `cbc:IssueDate` |
| Currency (must equal the PO currency) | `cbc:DocumentCurrencyCode` |
| Purchase order | `cac:OrderReference/cbc:ID` (the ProcuraX PO number) |
| Registration number (適格請求書発行事業者登録番号) | `cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID` |
| Line → PO line | `cac:InvoiceLine/cac:OrderLineReference/cbc:LineID` (PO line number); in order when no line has a reference and the counts agree |
| Quantity | `cac:InvoiceLine/cbc:InvoicedQuantity` |
| Unit price | `cac:Price/cbc:PriceAmount` ÷ `cac:Price/cbc:BaseQuantity` (default 1) |
| Tax rate | `cac:Item/cac:ClassifiedTaxCategory/cbc:Percent` (10 → 0.10) |
| Tax amount | `cac:TaxTotal/cbc:TaxAmount` in the document currency |

XML is parsed with `defusedxml`; DTDs, entities and external references are refused (billion-laughs and
XXE samples are tested). Documents are limited to 5 MiB. A sample JP PINT invoice with a 10 % and an 8 %
line is in `backend/tests/fixtures/einvoice/jp_pint_invoice.xml`.

Not validated: the full JP PINT / Peppol BIS business rules (Schematron). ProcuraX extracts the fields it
matches on and rejects documents it cannot read; a production access point validates the rest before
delivery.

**NTA registry lookup.** With `PROCURAX_NTA_REGISTRY_ENABLED=true` and an application ID from the National
Tax Agency, each invoice's registration number is looked up through the NTA qualified-invoice issuer
publication Web-API (version 1, `num` endpoint, JSON). Answers are cached with their fetch time
(`PROCURAX_NTA_CACHE_HOURS`, default 24) in `qualified_invoice_registry_cache` (public data, shared across
tenants). A number that was not registered, revoked or lapsed **on the invoice date** makes the match an
exception for finance to review. If the API is disabled or unreachable, a stale cached answer is used if
there is one, otherwise the previous checks apply (check digit + vendor master); the source used
(`nta_web_api`, `cache`, `cache_stale`, `unavailable`, `disabled`) is stored with the match. The website is
never scraped.

The adapter was tested against a local mock that serves the documented JSON shape. **It has not been run
against the real Web-API**, which needs an application ID; the request parameters and field names must be
confirmed once one is issued.

## Sending over the Peppol network (out of scope)

ProcuraX does not send or receive over the Peppol network itself. Connecting to it needs:

1. **A certified Peppol access point.** Either become one (Peppol membership and an agreement with the
   Japan Peppol Authority, the Digital Agency, plus testing and certification) or, far more commonly,
   contract a certified service provider and use its API.
2. **Participant registration.** The receiving organisation is registered in an SMP (Service Metadata
   Publisher) under its identifier (in Japan typically scheme `0188`, the corporate number), discoverable
   through the SML.
3. **Transport.** AS4 messaging with the access point's Peppol PKI certificates (handled by the provider).
4. **Validation.** JP PINT validation artefacts (Schematron) before sending and on receipt, with
   rejection messages back to the sender (Message Level Response / Invoice Response).
5. **Mapping out.** For sending, generating UBL from ProcuraX invoices (we only receive invoices from
   suppliers today, so this would apply to credit notes or self-billing).
6. **Operations.** Retries and dead letters (the outbox worker already provides the pattern), archiving
   of the original XML with its hash (invoice-file storage can hold it), and monitoring.

A provider integration would call its inbound API (or receive its webhook) and post each document to
`/api/v1/einvoices` on the tenant's behalf.
