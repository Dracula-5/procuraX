# ProcuraX transformation case

This case is a solution-design exercise for a fictional Japanese manufacturing client. It is not a real client engagement, and it contains no claims of realized savings or user adoption.

## 1. Client situation

A multi-department manufacturer operates procurement across e-mail, spreadsheets, ERP extracts and Japanese ringi approval documents. The CFO wants better control of discretionary spend and more reliable audit evidence, while business teams need purchases to move faster.

## 2. Current state

The working hypothesis is a fragmented purchase-to-pay process: employees raise demand in different channels; approvers review inconsistent context; procurement checks vendors and quotes manually; finance receives invoice data after commitments have already been made. This is a hypothesis to validate through interviews, process observation and transaction sampling before implementation.

## 3. Business challenges and root causes

| Challenge | Likely root cause | Evidence to collect |
|---|---|---|
| Slow approval cycles | Requests arrive with missing context; routing depends on e-mail and informal thresholds | Timestamp sample from request to final decision; rework reasons |
| Weak spend visibility | Vendor, category and cost-centre data are incomplete or reconciled monthly | ERP/AP extracts, master-data completeness, close calendar |
| Off-contract buying | Approved catalogues and contract status are hard to find at intake | PO-to-contract match sample and buying-channel interviews |
| Invoice rework | PO, receipt and invoice identifiers and line data are reconciled manually | Exception codes, touch time and mismatch rate |
| Audit reconstruction | Approval evidence lives in separate systems and mailboxes | Audit request sample and evidence retrieval time |

Do not assume that software alone fixes process ownership, master data, or policy ambiguity.

## 4. Transformation objectives

1. Create one governed intake path for addressable spend.
2. Apply published policy consistently and retain the exact decision evidence.
3. Make approved vendors and contract status visible at the buying decision.
4. Match orders, receipts and invoices with explicit exception ownership.
5. Measure cycle time, exception rate and spend coverage before discussing savings.

## 5. Future state

Employee request → deterministic policy and budget checks → role-based approval → procurement sourcing / PO → vendor fulfilment → receipt → invoice capture and match → finance review → ERP/payment hand-off → spend analytics and audit. High-value approvals and payment remain human decisions. AI can prioritize, extract or recommend only after representative data and evaluation are available.

## 6. Options assessment

Indicative relative assessment; validate with the client’s ERP, data, controls and operating model.

| Option | Cost | Complexity | Risk | Value | Data need | Scale |
|---|---|---|---|---|---|---|
| A. Traditional workflow automation | Low–medium | Low–medium | Low; limited intelligence, possible workflow silos | Faster intake and routing | Policies, org chart, vendor master | Moderate; integration boundaries matter |
| B. AI-assisted procurement | Medium | Medium | Medium; model error and data quality risk | Better extraction, classification and decision support | Historical labels, invoices, outcomes | High if models are monitored and governed |
| C. Intelligent platform with integrated workflow + AI | High | High | Highest change and integration risk | Broad process visibility and automation potential | Clean master data, ERP interfaces, feedback labels | High after staged rollout |

## 7. Recommendation

Start with Option A’s governed workflow and data foundation, then add narrowly scoped, evaluated AI where the business case is measurable. ProcuraX follows that sequence: the deterministic policy engine controls routing; an approval creates the decision record; document extraction and ranking are planned as advisory capabilities. This limits financial decision risk while creating structured data for later automation. A big-bang intelligent platform is not justified until process ownership, integration requirements, data quality and expected benefits are validated.

## 8. Value case and KPIs

Agree the baseline and target with the client. Candidate measures include request-to-decision median and p90 time, invoice first-pass match rate, manual invoice touch time, spend under contract, policy exception rate, duplicate invoice block rate, audit evidence retrieval time, and addressable spend visibility. Savings require a controlled comparison that accounts for category mix, market price, volume and seasonality. No ROI or target percentage is claimed here.

## 9. Delivery approach

Pilot one department and a limited set of categories. Map policy and roles; clean vendor and cost-centre data; integrate identity and ERP read/write boundaries; train users; run shadow reporting before enabling controls; measure a baseline; then expand by country and process. Retain a rollback path to the existing approval process until controls and reconciliation are proven.

## 10. Risks and assumptions

- ERP APIs, finance controls, data residency, Japanese privacy requirements and integration ownership need client discovery.
- Process metrics and exception taxonomies may be inconsistent across departments.
- Employee change management, supplier adoption, contracts and local-language support affect adoption.
- The fictional case assumes a tenant can provide a defined organization hierarchy, budgets, approved-vendor rules and transaction currency.
- AI evaluation datasets must be representative, licensed and reviewed for sensitive data before use.
