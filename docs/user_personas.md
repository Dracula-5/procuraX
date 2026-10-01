# Who uses ProcuraX: illustrative personas and demo walkthroughs

> **Illustrative, not research.** These personas are composites written to show who a procurement
> platform serves in a Japanese mid-sized manufacturer. No interviews, pilot or usage study were
> conducted, and nothing here is evidence of user needs or outcomes. Every person and company is
> fictional and matches the demo tenant *ProcuraX Demo Manufacturing K.K. (fictional)*, so each
> walkthrough can be followed by signing in as that persona on the login page.

## The organisation in one paragraph

A manufacturer with about 300 people and an April–March fiscal year. Purchases start as e-mails or
Excel forms, approvals travel as ringi (稟議) circulation with hanko stamps, buyers chase quotes by
e-mail, and finance keys invoices into the ERP and checks them against delivery notes by eye. Since
October 2023, every supplier invoice must also be checked for a qualified-invoice registration
number (インボイス制度), which added manual work in accounts payable.

## Personas

### Haruto Sato: network engineer (requester)
- **Wants:** the switches for the new line without learning procurement rules.
- **Today:** fills in an Excel form, e-mails it, and asks around to find out where it is stuck.
- **In ProcuraX:** one request form with line items and cost centre. The policy preview tells him
  before submitting which approvals apply and why. The timeline shows exactly who holds it.
- **Walkthrough:** Sign in as Haruto → *New request* → add two hardware lines → watch the policy
  panel cite rule IDs → submit → open the request timeline.

### Yui Nakamura: IT infrastructure manager (first approver)
- **Wants:** to approve quickly without missing budget or policy problems, including while travelling.
- **Today:** approves from e-mail with no budget context; requests wait when she is away.
- **In ProcuraX:** an approval inbox with due dates, the policy evaluation attached, budget impact
  shown. Overdue steps escalate automatically to the next eligible manager. Delegating a step to a
  colleague while away is supported by the API; the screen for it is not built yet.
- **Walkthrough:** Sign in as Yui → *Approval inbox* → open a pending request → read the cited rules →
  approve or reject with a comment.

### Kenji Watanabe: head of IT & Digital (department head)
- **Wants:** control of the department budget and visibility of large commitments.
- **In ProcuraX:** high-value requests route to him by policy, not by memory. Budgets are
  re-checked under a lock at final approval, so two approvals cannot jointly overspend.
- **Walkthrough:** Sign in as Kenji → *Approval inbox* → approve a high-value request → *Budgets* to see utilisation.

### Mei Takahashi: procurement officer (buyer)
- **Wants:** fair, defensible supplier choices and a clean PO-to-invoice trail.
- **Today:** compares quotes in a spreadsheet; the reason for a choice lives in her head.
- **In ProcuraX:** records quotes (optionally with per-line prices). The transparent baseline ranks
  eligible quotes; choosing another supplier requires a written reason, which is kept. POs carry
  the awarded prices. Receipts are recorded per line, and invoices are matched with tax and
  registration-number checks. An OCR preview reads scanned invoices for her to verify.
- **Walkthrough:** Sign in as Mei → *Purchase to pay* → *Quote comparison*: record two quotes and award
  the second-ranked one to see the override prompt → *Create PO* → mark sent and acknowledged →
  *Receive line* → *Add invoice lines* with tax rate 0.10 and a registration number → see the match
  result → *Request payment approval* on a matched invoice.

### Hina Yoshida: finance manager (exceptions and payments)
- **Wants:** to pay only what was ordered and received, once, and to prove it to auditors.
- **Today:** spots duplicates and over-billing by eye; prepares the bank transfer file by hand.
- **In ProcuraX:** mismatched invoices (price, over-billed quantity, tax outside the lawful
  rounding range, wrong or missing registration number) wait in an exception queue for her
  decision. Approved payments are exported as a Zengin 総合振込 file for upload to the bank. The
  system never sends money itself.
- **Walkthrough** (after Mei's, which creates the invoice and payment request): Sign in as Hina →
  *Purchase to pay* → *Invoice match queue*: approve or reject an
  exception with a comment → *Payment approvals*: approve a payment raised by someone else → *Export
  bank transfer file*.

### Sota Kato: finance analyst (audit and control)
- **Wants:** to reconstruct any decision months later without digging through inboxes.
- **In ProcuraX:** an append-only audit trail and per-request timelines, with the policy version
  that applied at the time.
- **Walkthrough:** Sign in as Sota → *Audit trail* → filter by a request → follow it from submission to payment.

### Takumi Matsumoto: business analyst (read-only)
- **Wants:** spend visibility without month-end exports, and evidence about whether the
  recommendations help.
- **In ProcuraX:** live spend by category, department and vendor, cycle-time percentiles, CSV
  export, and the *Recommendation vs decision vs outcome* card comparing followed and overridden awards.
- **Walkthrough:** Sign in as Takumi → *Dashboard* → filter by department → export CSV → *Purchase to pay*
  → read the decision-support card (demo data, not pilot evidence).

### Aoi Tanaka: organisation administrator
- **Wants:** to set up people, structure and policy without IT tickets.
- **In ProcuraX:** invitations, roles, departments and cost centres, versioned approval
  thresholds, the company's bank details for transfer files, and policy documents for the assistant.
  The assistant now retrieves Japanese text, for example the NTA's invoice-system guidance.
- **Walkthrough:** Sign in as Aoi → *Users & roles* → invite a colleague → *Approval policy* → view
  versions → *Procurement assistant* → *Organisation guidelines*: paste a Japanese guideline → ask a
  question in Japanese under *Policy and guidance search* and read the cited excerpt.

## What a real pilot would add

Personas describe intent; only real users show whether the product helps. A future pilot would give
3–5 people from these roles scripted tasks (the walkthroughs above), and record task time, errors,
help requests and a System Usability Scale questionnaire, with consent. Genuine usage would be kept
separate from the demo tenant. Nothing like that has been run, so this repository makes no claims about
user adoption or satisfaction.
