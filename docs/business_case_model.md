# Procurement transformation business case model

**Status: parameterized model and calculator delivered; no scenario inputs or client results are populated.**

A standard-library-compatible Python CLI calculates supplied cases: from `backend/`, run
`python -m app.scripts.calculate_business_case path/to/scenarios.json`. Each input must carry a
numeric `value`, a `kind` (`measured` or `assumption`), and a non-empty `source`; the tool reports
how many inputs remain assumptions. It does not bundle default volumes, costs or savings.

Use this model to compare a current manual process with a digitized process. Populate every input from a time study, finance data, vendor quote, or a clearly labelled assumption. Keep measured and assumed values in separate versions of the workbook or analysis.

## Inputs

| Input | Unit | Source |
|---|---|---|
| Annual purchase requests | requests/year | Procurement system or stated scenario assumption |
| Annual invoices | invoices/year | Finance system or stated scenario assumption |
| Current request handling time | minutes/request | Time study; separate touch time from waiting time |
| Digitized request handling time | minutes/request | Pilot telemetry or labelled process assumption |
| Current invoice handling time | minutes/invoice | Time study |
| Digitized invoice handling time | minutes/invoice | Pilot telemetry or labelled process assumption |
| Invoice exception rate, current and digitized | share of invoices | Invoice records; use the same exception definition |
| Incremental exception handling effort | minutes/exception | Time study |
| Loaded labor cost | currency/hour | Finance-approved rate |
| Annual platform, hosting, support and integration cost | currency/year | Supplier quotes and operating plan |
| One-time implementation cost | currency | Project estimate or actuals |
| Verified duplicate-payment loss avoided | currency/year | Finance-confirmed prevented payments only |

Do not count cycle-time reduction as labor savings unless it reduces paid effort or creates a separately evidenced capacity benefit. Do not count an invoice as a prevented duplicate payment until finance confirms the counterfactual payment would otherwise have occurred.

## Calculations

For either current or digitized process:

- Request labor hours = annual requests × request handling minutes ÷ 60.
- Invoice labor hours = annual invoices × (base handling minutes + exception rate × incremental exception minutes) ÷ 60.
- Processing labor cost = (request labor hours + invoice labor hours) × loaded labor cost per hour.
- Annual net benefit = current processing labor cost − digitized processing labor cost + verified duplicate-payment loss avoided − annual platform, hosting, support and integration cost.
- First-year net benefit = annual net benefit − one-time implementation cost.
- Payback months = one-time implementation cost ÷ (annual net benefit ÷ 12), only when annual net benefit is positive.

Report capacity released separately from cash savings. If labor is redeployed rather than removed, report hours released and the destination work; do not label it cost reduction.

## Scenario discipline

Create conservative, expected and aggressive cases by changing only visible inputs. Record the owner, source, date, range and confidence for each assumption. The expected case is not a forecast until the client validates its assumptions. Run sensitivity analysis for transaction volume, exception rate, handling time, labor cost and annual operating cost.

| Case | Input set / source | Annual net benefit | First-year net benefit | Payback |
|---|---|---:|---:|---:|
| Conservative | To be supplied | Not calculated | Not calculated | Not calculated |
| Expected | To be supplied | Not calculated | Not calculated | Not calculated |
| Aggressive | To be supplied | Not calculated | Not calculated | Not calculated |

The CLI expects `{"scenarios": [{"name": "Conservative", "inputs": { ... }}]}`. Input keys are
`annual_requests`, `annual_invoices`, `current_request_minutes`, `digitized_request_minutes`,
`current_invoice_minutes`, `digitized_invoice_minutes`, `current_invoice_exception_rate`,
`digitized_invoice_exception_rate`, `extra_minutes_per_exception`,
`loaded_labor_cost_per_hour`, `annual_platform_cost`, `implementation_cost`, and
`verified_duplicate_loss_avoided`. Each value uses the sourced-number shape described above.

## Measurement and controls

Before/after comparisons require a defined observation window, consistent process boundaries, comparable transaction cohorts and documented exclusions. Record the raw source, calculation version, timestamps and reviewer. Separate observed results, controlled simulations and assumptions in every presentation. ProcuraX currently has no populated cost inputs, completed pilot or realized savings result.
