import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/useAuth";
import { Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { date, money } from "../lib/format";

type OrderLine = { id: string; line_no: number; description: string; quantity: string; unit_price: string; line_total: string };
type Order = { id: string; number: string; request_id: string; vendor_id: string; status: string; currency: string; total: string; awarded_quote_id: string | null; items: OrderLine[]; created_at: string };
type Invoice = { id: string; invoice_number: string; purchase_order_id: string; total: string; currency: string; match_status: string; invoice_date: string; registration_number: string | null; match_details: { exceptions?: string[]; qualified_invoice?: { status: string }; tax?: { status: string } }; line_match_details: { line_no: number; price_status: string; quantity_status: string }[] };
type InvoiceLineInput = { purchase_order_item_id: string; quantity: string; unit_price: string; tax_rate?: string };
type Request = { id: string; number: string; title: string; status: string; estimated_total: string; currency: string; preferred_vendor_id: string | null };
type Payment = { id: string; invoice_id: string; amount: string; currency: string; status: string; comment: string | null; export_reference: string | null };
type TransferFile = { reference: string; filename: string; payments: number; total: number; skipped: { payment_id: string; reason: string }[]; content_base64: string };

function download(file: TransferFile) {
  const bytes = Uint8Array.from(atob(file.content_base64), ch => ch.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function PurchaseToPay() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const orders = useQuery({ queryKey: ["purchase-orders"], queryFn: () => api.get<Order[]>("/purchase-orders") });
  const invoices = useQuery({ queryKey: ["invoices"], queryFn: () => api.get<Invoice[]>("/invoices") });
  const payments = useQuery({ queryKey: ["payments"], queryFn: () => api.get<Payment[]>("/payments") });
  const requests = useQuery({ queryKey: ["approved-requests"], queryFn: () => api.get<{ items: Request[]; total: number }>("/purchase-requests", { status: ["approved"], limit: 100 }) });
  const create = useMutation({
    mutationFn: (id: string) => api.post<Order>(`/purchase-requests/${id}/purchase-order`),
    onSuccess: () => { setError(""); void qc.invalidateQueries({ queryKey: ["purchase-orders"] }); void qc.invalidateQueries({ queryKey: ["approved-requests"] }); },
    onError: (e: Error) => setError(e.message),
  });
  const receive = useMutation({
    mutationFn: ({ id, purchase_order_item_id, quantity }: { id: string; purchase_order_item_id: string; quantity: number }) => api.post(`/purchase-orders/${id}/receipts`, { received_on: new Date().toISOString().slice(0, 10), purchase_order_item_id, quantity, damaged_quantity: 0, comments: "Recorded in ProcuraX" }),
    onSuccess: () => { setError(""); void qc.invalidateQueries({ queryKey: ["purchase-orders"] }); },
    onError: (e: Error) => setError(e.message),
  });
  const invoice = useMutation({
    mutationFn: ({ id, invoice_number, items, tax, registration_number }: { id: string; invoice_number: string; items: InvoiceLineInput[]; tax: string; registration_number: string | null }) => api.post(`/purchase-orders/${id}/invoices`, { invoice_number, invoice_date: new Date().toISOString().slice(0, 10), items, tax, registration_number }),
    onSuccess: () => { setError(""); void qc.invalidateQueries({ queryKey: ["invoices"] }); },
    onError: (e: Error) => setError(e.message),
  });
  const transition = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => api.post(`/purchase-orders/${id}/transition`, { status }),
    onSuccess: () => { setError(""); void qc.invalidateQueries({ queryKey: ["purchase-orders"] }); },
    onError: (e: Error) => setError(e.message),
  });
  const requestPayment = useMutation({
    mutationFn: (id: string) => api.post(`/invoices/${id}/payment-requests`),
    onSuccess: () => { setError(""); void qc.invalidateQueries({ queryKey: ["payments"] }); },
    onError: (e: Error) => setError(e.message),
  });
  const decidePayment = useMutation({
    mutationFn: (id: string) => api.post(`/payments/${id}/decision`, { decision: "approve", comment: "Reviewed in ProcuraX" }),
    onSuccess: () => { setError(""); void qc.invalidateQueries({ queryKey: ["payments"] }); },
    onError: (e: Error) => setError(e.message),
  });
  const exportTransfers = useMutation({
    mutationFn: (transfer_date: string) => api.post<TransferFile>("/payments/transfer-file", { transfer_date }),
    onSuccess: file => { setError(file.skipped.length ? `Exported ${file.payments} payment(s) as ${file.reference}; skipped: ${file.skipped.map(s => s.reason).join("; ")}` : ""); download(file); void qc.invalidateQueries({ queryKey: ["payments"] }); },
    onError: (e: Error) => setError(e.message),
  });
  const decideInvoiceException = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) => api.post(`/invoices/${id}/exception-decision`, { decision, comment: decision === "approve" ? "Exception verified and approved in ProcuraX." : "Exception reviewed and rejected in ProcuraX." }),
    onSuccess: () => { setError(""); void qc.invalidateQueries({ queryKey: ["invoices"] }); },
    onError: (e: Error) => setError(e.message),
  });

  return <>
    <PageHeader title="Purchase to pay" subtitle="Convert approved demand into a controlled order, record receipt, and compare invoice totals against the order and accepted quantity." />
    {error && <ErrorNotice error={error} />}
    {requests.isLoading || orders.isLoading || invoices.isLoading || payments.isLoading ? <Spinner label="Loading purchasing records" /> : null}
    {requests.isError || orders.isError || invoices.isError || payments.isError ? <ErrorNotice error={requests.error ?? orders.error ?? invoices.error ?? payments.error} /> : null}
    <div className="grid gap-5 xl:grid-cols-2">
      <Card title="Approved requests ready for ordering">
        {!requests.data?.items.length ? <EmptyState title="No approved requests ready">Requests need an approved vendor before procurement can issue a PO.</EmptyState> :
          <Table><thead><tr><Th>Request</Th><Th>Value</Th><Th /></tr></thead><tbody>{requests.data.items.filter(r => r.preferred_vendor_id).map(r => <tr key={r.id}><Td><p className="font-medium">{r.number}</p><p className="text-xs text-muted">{r.title}</p></Td><Td>{money(r.estimated_total, r.currency)}</Td><Td>{can("vendor:manage") && <Button size="sm" loading={create.isPending} onClick={() => create.mutate(r.id)}>Create PO</Button>}</Td></tr>)}</tbody></Table>}
      </Card>
      <Card title="Purchase orders">
        {!orders.data?.length ? <EmptyState title="No purchase orders yet">POs are issued from approved purchase requests.</EmptyState> :
          <Table><thead><tr><Th>PO</Th><Th>Vendor</Th><Th>Value</Th><Th>State</Th><Th>Action</Th></tr></thead><tbody>{orders.data.map(o => <tr key={o.id}><Td><span className="font-medium">{o.number}</span><p className="text-xs text-muted">{o.items.map(item => `${item.description} × ${item.quantity}`).join(" · ")}</p><p className="text-xs text-muted">{date(o.created_at)}{o.awarded_quote_id ? " · awarded quote" : ""}</p></Td><Td className="font-mono text-xs">{o.vendor_id.slice(0, 8)}</Td><Td>{money(o.total, o.currency)}</Td><Td>{o.status.replaceAll("_", " ")}</Td><Td>{can("vendor:manage") && <div className="flex flex-wrap gap-2">{o.status === "approved" && <Button size="sm" variant="secondary" onClick={() => transition.mutate({ id: o.id, status: "sent" })}>Mark sent</Button>}{o.status === "sent" && <Button size="sm" variant="secondary" onClick={() => transition.mutate({ id: o.id, status: "acknowledged" })}>Acknowledge</Button>}{["acknowledged", "partially_received"].includes(o.status) && <Button size="sm" variant="secondary" onClick={() => { const lineChoice = window.prompt(`Line number to receive:\n${o.items.map(item => `${item.line_no}: ${item.description} (ordered ${item.quantity})`).join("\n")}`); const line = o.items.find(item => item.line_no === Number(lineChoice)); const quantity = Number(window.prompt("Quantity received for this line?")); if (line && quantity > 0) receive.mutate({ id: o.id, purchase_order_item_id: line.id, quantity }); }}>Receive line</Button>}{["partially_received", "received"].includes(o.status) && <Button size="sm" variant="secondary" onClick={() => { const invoice_number = window.prompt("Vendor invoice number"); if (!invoice_number) return; const items = o.items.flatMap((item): InvoiceLineInput[] => { const quantity = Number(window.prompt(`Invoice quantity for ${item.description} (0 to skip)`, item.quantity)); if (quantity <= 0) return []; const unit_price = Number(window.prompt(`Invoice unit price for ${item.description}`, item.unit_price)); const rate = window.prompt(`Consumption tax rate for ${item.description}: 0.10, 0.08, 0 or blank if not stated`, "0.10")?.trim(); return unit_price >= 0 ? [{ purchase_order_item_id: item.id, quantity: String(quantity), unit_price: String(unit_price), ...(rate ? { tax_rate: rate } : {}) }] : []; }); if (!items.length) return; const tax = window.prompt("Consumption tax stated on the invoice", "0")?.trim() || "0"; const registration_number = window.prompt("Qualified invoice registration number (T + 13 digits), blank if none")?.trim() || null; invoice.mutate({ id: o.id, invoice_number, items, tax, registration_number }); }}>Add invoice lines</Button>}</div>}</Td></tr>)}</tbody></Table>}
      </Card>
      <Card title="Invoice match queue" className="xl:col-span-2">
        {!invoices.data?.length ? <EmptyState title="No invoices submitted">Invoice matching begins after a PO has a recorded receipt and invoice metadata.</EmptyState> :
          <Table><thead><tr><Th>Invoice</Th><Th>PO</Th><Th>Date</Th><Th>Amount</Th><Th>Match result</Th><Th>Line checks</Th><Th /></tr></thead><tbody>{invoices.data.map(i => <tr key={i.id}><Td className="font-medium">{i.invoice_number}</Td><Td className="font-mono text-xs">{i.purchase_order_id.slice(0, 8)}</Td><Td>{date(i.invoice_date)}</Td><Td>{money(i.total, i.currency)}</Td><Td><span className="rounded-full bg-surface-2 px-2 py-1 text-xs">{i.match_status.replaceAll("_", " ")}</span></Td><Td className="text-xs text-muted"><p>{i.line_match_details.map(line => `L${line.line_no}: ${line.price_status}, ${line.quantity_status.replaceAll("_", " ")}`).join(" · ") || "—"}</p>{i.match_details.qualified_invoice && i.match_details.qualified_invoice.status !== "not_applicable" && <p>Registration: {i.match_details.qualified_invoice.status.replaceAll("_", " ")}{i.match_details.tax?.status && i.match_details.tax.status !== "not_compared" ? ` · Tax: ${i.match_details.tax.status}` : ""}</p>}{i.match_details.exceptions?.map(reason => <p key={reason} className="text-danger">{reason}</p>)}</Td><Td><div className="flex flex-wrap gap-2">{can("payment:approve") && ["mismatch", "partially_matched", "requires_review"].includes(i.match_status) && <><Button size="sm" variant="secondary" loading={decideInvoiceException.isPending} onClick={() => decideInvoiceException.mutate({ id: i.id, decision: "approve" })}>Approve exception</Button><Button size="sm" variant="secondary" loading={decideInvoiceException.isPending} onClick={() => decideInvoiceException.mutate({ id: i.id, decision: "reject" })}>Reject exception</Button></>}{can("purchase_request:read_all") && ["matched", "exception_approved"].includes(i.match_status) && !payments.data?.some(p => p.invoice_id === i.id) && <Button size="sm" variant="secondary" onClick={() => requestPayment.mutate(i.id)}>Request payment approval</Button>}</div></Td></tr>)}</tbody></Table>}
      </Card>
      <Card title="Payment approvals" className="xl:col-span-2">
        {can("payment:approve") && payments.data?.some(p => p.status === "approved") && <div className="mb-3 flex flex-wrap items-center gap-3"><Button size="sm" loading={exportTransfers.isPending} onClick={() => { const next = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10); const day = window.prompt("Transfer date (YYYY-MM-DD) for the Zengin bank file", next)?.trim(); if (day) exportTransfers.mutate(day); }}>Export bank transfer file (Zengin)</Button><span className="text-xs text-muted">Creates a Shift_JIS 総合振込 file for upload to the bank. ProcuraX does not send money.</span></div>}
        {!payments.data?.length ? <EmptyState title="No payment approvals">Only matched invoices can enter the finance approval queue.</EmptyState> :
          <Table><thead><tr><Th>Invoice</Th><Th>Value</Th><Th>State</Th><Th>Decision</Th></tr></thead><tbody>{payments.data.map(p => <tr key={p.id}><Td className="font-mono text-xs">{p.invoice_id.slice(0, 8)}</Td><Td>{money(p.amount, p.currency)}</Td><Td>{p.status.replaceAll("_", " ")}{p.export_reference && <p className="text-xs text-muted">{p.export_reference}</p>}</Td><Td>{can("payment:approve") && p.status === "pending_approval" ? <Button size="sm" loading={decidePayment.isPending} onClick={() => decidePayment.mutate(p.id)}>Approve payment</Button> : p.comment ?? "—"}</Td></tr>)}</tbody></Table>}
      </Card>
      <QuoteSourcing canManage={can("vendor:manage")} />
      {can("analytics:read") && <DecisionSupport />}
      {can("vendor:manage") && <InvoiceOCR />}
    </div>
  </>;
}

type AwardContext = { followed_recommendation: boolean; override_reason: string | null; awarded_rank: number | null };
type Quote = { id: string; vendor_id: string; vendor_name: string; amount: string; currency: string; delivery_days: number; quality_score: string; contract_compliant: boolean; is_awarded: boolean; award_context: AwardContext | null; eligible: boolean; rank: number | null; score: number | null; reasons: string[] };
type VendorOption = { id: string; name: string };

function QuoteSourcing({ canManage }: { canManage: boolean }) {
  const [requestId, setRequestId] = useState("");
  const [vendorId, setVendorId] = useState("");
  const [amount, setAmount] = useState("");
  const [delivery, setDelivery] = useState("10");
  const [quality, setQuality] = useState("3");
  const [contract, setContract] = useState(false);
  const qc = useQueryClient();
  const pending = useQuery({ queryKey: ["sourcing-requests"], queryFn: () => api.get<{ items: Request[]; total: number }>("/purchase-requests", { status: ["pending_approval", "approved"], limit: 100 }), enabled: canManage });
  const vendors = useQuery({ queryKey: ["approved-vendors"], queryFn: () => api.get<{ items: VendorOption[]; total: number }>("/vendors", { status: "approved", limit: 200 }), enabled: canManage });
  const quotes = useQuery({ queryKey: ["quotes", requestId], queryFn: () => api.get<{ method: string; weights: Record<string, string>; recommended_quote_id: string | null; quotes: Quote[] }>(`/purchase-requests/${requestId}/quotes`), enabled: Boolean(requestId) });
  const record = useMutation({
    mutationFn: () => api.post(`/purchase-requests/${requestId}/quotes`, { vendor_id: vendorId, amount, delivery_days: Number(delivery), quality_score: quality, contract_compliant: contract }),
    onSuccess: () => { setAmount(""); void qc.invalidateQueries({ queryKey: ["quotes", requestId] }); },
  });
  const award = useMutation({
    mutationFn: ({ quoteId, override_reason }: { quoteId: string; override_reason?: string }) => api.post(`/purchase-requests/${requestId}/quotes/${quoteId}/award`, override_reason ? { override_reason } : undefined),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["quotes", requestId] }); void qc.invalidateQueries({ queryKey: ["approved-requests"] }); void qc.invalidateQueries({ queryKey: ["sourcing-requests"] }); void qc.invalidateQueries({ queryKey: ["decision-support"] }); },
  });
  const selectedRequest = pending.data?.items.find(request => request.id === requestId);
  const onSubmit = (event: FormEvent) => { event.preventDefault(); if (requestId && vendorId && Number(amount) >= 0) record.mutate(); };
  const onAward = (quote: Quote) => {
    if (quote.id === quotes.data?.recommended_quote_id) { award.mutate({ quoteId: quote.id }); return; }
    const reason = window.prompt("This quote is not the top-ranked recommendation. Record why you are overriding it (at least 10 characters):")?.trim();
    if (reason) award.mutate({ quoteId: quote.id, override_reason: reason });
  };
  return <Card title="Quote comparison" className="xl:col-span-2">
    <p className="mb-4 text-sm text-muted">Transparent rule baseline: price 40%, delivery 20%, quality 25%, contract compliance 15%, over eligible quotes only. The top-ranked quote is a recommendation; awarding another quote requires a recorded override reason, and both are kept for outcome review.</p>
    {canManage && <div className="mb-4 grid gap-3 md:grid-cols-3">
      <Field label="Submitted request"><Select value={requestId} onChange={e => setRequestId(e.target.value)}><option value="">Choose request</option>{pending.data?.items.map(r => <option key={r.id} value={r.id}>{r.number} · {r.title}</option>)}</Select></Field>
      <Field label="Approved vendor"><Select value={vendorId} onChange={e => setVendorId(e.target.value)}><option value="">Choose vendor</option>{vendors.data?.items.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</Select></Field>
      <form className="flex items-end gap-2" onSubmit={onSubmit}><Field label="Quote amount"><Input type="number" min="0" step="0.01" required value={amount} onChange={e => setAmount(e.target.value)} /></Field><Button size="sm" loading={record.isPending} disabled={!requestId || !vendorId}>Record</Button></form>
      <Field label="Delivery days"><Input type="number" min="0" value={delivery} onChange={e => setDelivery(e.target.value)} /></Field>
      <Field label="Quality score (0–5)"><Input type="number" min="0" max="5" step="0.1" value={quality} onChange={e => setQuality(e.target.value)} /></Field>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={contract} onChange={e => setContract(e.target.checked)} /> Contract compliant</label>
    </div>}
    {record.isError && <ErrorNotice error={record.error} />}
    {!requestId ? <EmptyState title="Select a submitted request">Recorded vendor quotes will be ranked with visible factors and reasons.</EmptyState> : quotes.isLoading ? <Spinner label="Comparing quotes" /> : quotes.isError ? <ErrorNotice error={quotes.error} /> : !quotes.data?.quotes.length ? <EmptyState title="No quotes recorded for this request" /> :
      <><Table><thead><tr><Th>Rank</Th><Th>Vendor</Th><Th>Quote</Th><Th>Delivery</Th><Th>Score</Th><Th>Evidence</Th><Th /></tr></thead><tbody>{quotes.data.quotes.map(q => <tr key={q.id} className={q.eligible ? "" : "opacity-60"}><Td>{q.rank ?? "—"}{q.id === quotes.data.recommended_quote_id && <p className="text-xs font-medium text-primary">Recommended</p>}</Td><Td className="font-medium">{q.vendor_name}</Td><Td>{money(q.amount, q.currency)}</Td><Td>{q.delivery_days} days</Td><Td>{q.score === null ? "Ineligible" : `${q.score.toFixed(1)} / 100`}</Td><Td className="text-xs text-muted">{q.reasons.join(" · ")}{q.award_context && <p>{q.award_context.followed_recommendation ? "Awarded as recommended" : `Override: ${q.award_context.override_reason ?? ""}`}</p>}</Td><Td>{canManage && selectedRequest?.status === "approved" && <Button size="sm" variant="secondary" loading={award.isPending} disabled={!q.eligible || q.is_awarded || quotes.data.quotes.some(row => row.is_awarded)} onClick={() => onAward(q)}>{q.is_awarded ? "Awarded" : "Award quote"}</Button>}</Td></tr>)}</tbody></Table>{award.isError && <ErrorNotice error={award.error} />}</>}
  </Card>;
}

type OCRResult = { status: string; filename: string; extracted_fields: Record<string, string | null>; raw_text: string; source: string };

function InvoiceOCR() {
  const [file, setFile] = useState<File | null>(null);
  const extraction = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Select an invoice PDF or image first.");
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      return api.post<OCRResult>("/procurement-assistant/invoice-ocr", { filename: file.name, content_base64: btoa(binary) });
    },
  });
  return <Card title="Invoice OCR preview" className="xl:col-span-2">
    <p className="mb-3 text-sm text-muted">Local Tesseract extracts visible text from PDF, PNG, JPEG, or TIFF. Extracted fields need human verification before invoice entry; files are processed in temporary storage and discarded.</p>
    <div className="flex flex-wrap items-end gap-3"><label className="text-sm">Invoice file<input type="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" onChange={event => setFile(event.target.files?.[0] ?? null)} className="mt-1 block text-sm" /></label><Button loading={extraction.isPending} disabled={!file} onClick={() => extraction.mutate()}>Extract fields</Button></div>
    <ErrorNotice error={extraction.error} />
    {extraction.data && <div className="mt-4 grid gap-4 lg:grid-cols-2"><Card title="Suggested fields"><dl className="space-y-2 text-sm">{Object.entries(extraction.data.extracted_fields).map(([key, value]) => <div key={key} className="flex justify-between gap-3"><dt className="text-muted">{key.replaceAll("_", " ")}</dt><dd className="text-right font-medium">{value ?? "Not extracted"}</dd></div>)}</dl></Card><Card title="OCR text for review"><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{extraction.data.raw_text}</pre></Card></div>}
  </Card>;
}


type OutcomeBucket = { awards: number; delivered: number; on_time: number; late: number; pending: number; on_time_rate: number | null; invoiced: number; first_pass_invoice_match_rate: number | null; damaged_quantity_rate: number | null };
type SourcingDecisions = { data_scope: string; awards_with_recommendation: number; followed: number; overridden: number; agreement_rate: number | null; outcomes: Record<"followed" | "overridden", OutcomeBucket>; overrides: { request_number: string; awarded_vendor: string; recommended_vendor: string | null; override_reason: string | null; delivery_outcome: string }[]; interpretation: string };

const pct = (value: number | null) => value === null ? "—" : `${(value * 100).toFixed(0)}%`;

function DecisionSupport() {
  const report = useQuery({ queryKey: ["decision-support"], queryFn: () => api.get<SourcingDecisions>("/decision-support/sourcing") });
  return <Card title="Recommendation vs decision vs outcome" className="xl:col-span-2">
    {report.isLoading ? <Spinner label="Loading decision records" /> : report.isError ? <ErrorNotice error={report.error} /> : !report.data?.awards_with_recommendation ? <EmptyState title="No recorded award decisions yet">Each award stores the baseline recommendation shown at the time, so later delivery and invoice outcomes can be compared.</EmptyState> :
      <>
        <p className="mb-3 text-sm text-muted">{report.data.data_scope}</p>
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          <div><p className="text-xs text-muted">Awards with recorded recommendation</p><p className="text-xl font-semibold">{report.data.awards_with_recommendation}</p></div>
          <div><p className="text-xs text-muted">Followed / overridden</p><p className="text-xl font-semibold">{report.data.followed} / {report.data.overridden}</p></div>
          <div><p className="text-xs text-muted">Agreement with baseline</p><p className="text-xl font-semibold">{pct(report.data.agreement_rate)}</p></div>
        </div>
        <Table><thead><tr><Th>Decision</Th><Th>Awards</Th><Th>On time</Th><Th>Late</Th><Th>Pending</Th><Th>First invoice matched</Th><Th>Damaged qty</Th></tr></thead><tbody>{(["followed", "overridden"] as const).map(key => { const b = report.data.outcomes[key]; return <tr key={key}><Td className="font-medium">{key === "followed" ? "Followed recommendation" : "Overrode recommendation"}</Td><Td>{b.awards}</Td><Td>{b.on_time} ({pct(b.on_time_rate)})</Td><Td>{b.late}</Td><Td>{b.pending}</Td><Td>{pct(b.first_pass_invoice_match_rate)}</Td><Td>{pct(b.damaged_quantity_rate)}</Td></tr>; })}</tbody></Table>
        {report.data.overrides.length > 0 && <div className="mt-4 space-y-2 text-sm">{report.data.overrides.map(o => <p key={o.request_number}><span className="font-medium">{o.request_number}</span>: awarded {o.awarded_vendor} over {o.recommended_vendor ?? "the recommendation"}: “{o.override_reason}” ({o.delivery_outcome.replaceAll("_", " ")})</p>)}</div>}
        <p className="mt-3 text-xs text-muted">{report.data.interpretation}</p>
      </>}
  </Card>;
}
