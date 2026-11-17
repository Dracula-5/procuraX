import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { date, money, percent } from "../lib/format";
import { useLabels } from "../lib/labels";

type OrderLine = { id: string; line_no: number; description: string; quantity: string; unit_price: string; line_total: string };
type Order = { id: string; number: string; request_id: string; vendor_id: string; status: string; currency: string; total: string; awarded_quote_id: string | null; items: OrderLine[]; created_at: string };
type Invoice = { id: string; invoice_number: string; purchase_order_id: string; total: string; currency: string; match_status: string; invoice_date: string; registration_number: string | null; match_details: { exceptions?: string[]; qualified_invoice?: { status: string }; tax?: { status: string } }; line_match_details: { line_no: number; price_status: string; quantity_status: string }[] };
type InvoiceLineInput = { purchase_order_item_id: string; quantity: string; unit_price: string; tax_rate?: string };
type Request = { id: string; number: string; title: string; status: string; estimated_total: string; currency: string; preferred_vendor_id: string | null };
type Payment = { id: string; invoice_id: string; amount: string; currency: string; status: string; comment: string | null; export_reference: string | null };
type TransferFile = { reference: string; filename: string; payments: number; total: number; skipped: { payment_id: string; reason: string }[]; content_base64: string };

const humanize = (value: string) => value.replaceAll("_", " ");

function download(file: TransferFile) {
  const bytes = Uint8Array.from(atob(file.content_base64), (ch) => ch.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function PurchaseToPay() {
  const { t } = useTranslation();
  const labels = useLabels();
  const { can } = useAuth();
  const qc = useQueryClient();
  // Keep the error object (not just its text) so ErrorNotice can translate API error codes.
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const onError = (e: unknown) => setError(e);
  const done = (key: string) => {
    setError(null);
    void qc.invalidateQueries({ queryKey: [key] });
  };
  const orders = useQuery({ queryKey: ["purchase-orders"], queryFn: () => api.get<Order[]>("/purchase-orders") });
  const invoices = useQuery({ queryKey: ["invoices"], queryFn: () => api.get<Invoice[]>("/invoices") });
  const payments = useQuery({ queryKey: ["payments"], queryFn: () => api.get<Payment[]>("/payments") });
  const requests = useQuery({ queryKey: ["approved-requests"], queryFn: () => api.get<{ items: Request[]; total: number }>("/purchase-requests", { status: ["approved"], limit: 100 }) });
  const create = useMutation({
    mutationFn: (id: string) => api.post<Order>(`/purchase-requests/${id}/purchase-order`),
    onSuccess: () => {
      done("purchase-orders");
      void qc.invalidateQueries({ queryKey: ["approved-requests"] });
    },
    onError,
  });
  const receive = useMutation({
    mutationFn: ({ id, purchase_order_item_id, quantity }: { id: string; purchase_order_item_id: string; quantity: number }) =>
      api.post(`/purchase-orders/${id}/receipts`, { received_on: new Date().toISOString().slice(0, 10), purchase_order_item_id, quantity, damaged_quantity: 0, comments: t("p2p.receiptComment") }),
    onSuccess: () => done("purchase-orders"),
    onError,
  });
  const invoice = useMutation({
    mutationFn: ({ id, invoice_number, items, tax, registration_number }: { id: string; invoice_number: string; items: InvoiceLineInput[]; tax: string; registration_number: string | null }) =>
      api.post(`/purchase-orders/${id}/invoices`, { invoice_number, invoice_date: new Date().toISOString().slice(0, 10), items, tax, registration_number }),
    onSuccess: () => done("invoices"),
    onError,
  });
  const transition = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => api.post(`/purchase-orders/${id}/transition`, { status }),
    onSuccess: () => done("purchase-orders"),
    onError,
  });
  const requestPayment = useMutation({
    mutationFn: (id: string) => api.post(`/invoices/${id}/payment-requests`),
    onSuccess: () => done("payments"),
    onError,
  });
  const decidePayment = useMutation({
    mutationFn: (id: string) => api.post(`/payments/${id}/decision`, { decision: "approve", comment: t("p2p.paymentComment") }),
    onSuccess: () => done("payments"),
    onError,
  });
  const exportTransfers = useMutation({
    mutationFn: (transfer_date: string) => api.post<TransferFile>("/payments/transfer-file", { transfer_date }),
    onSuccess: (file) => {
      setError(null);
      setNotice(file.skipped.length ? t("p2p.exportedWithSkips", { count: file.payments, reference: file.reference, reasons: file.skipped.map((s) => s.reason).join("; ") }) : "");
      download(file);
      void qc.invalidateQueries({ queryKey: ["payments"] });
    },
    onError,
  });
  const decideInvoiceException = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      api.post(`/invoices/${id}/exception-decision`, { decision, comment: decision === "approve" ? t("p2p.exceptionApprovedComment") : t("p2p.exceptionRejectedComment") }),
    onSuccess: () => done("invoices"),
    onError,
  });

  const receiveLine = (o: Order) => {
    const lineChoice = window.prompt(t("p2p.prompt.receiveLine", { lines: o.items.map((item) => t("p2p.prompt.lineOption", { no: item.line_no, description: item.description, quantity: item.quantity })).join("\n") }));
    const line = o.items.find((item) => item.line_no === Number(lineChoice));
    const quantity = Number(window.prompt(t("p2p.prompt.quantityReceived")));
    if (line && quantity > 0) receive.mutate({ id: o.id, purchase_order_item_id: line.id, quantity });
  };
  const addInvoice = (o: Order) => {
    const invoice_number = window.prompt(t("p2p.prompt.invoiceNumber"));
    if (!invoice_number) return;
    const items = o.items.flatMap((item): InvoiceLineInput[] => {
      const quantity = Number(window.prompt(t("p2p.prompt.invoiceQuantity", { description: item.description }), item.quantity));
      if (quantity <= 0) return [];
      const unit_price = Number(window.prompt(t("p2p.prompt.invoicePrice", { description: item.description }), item.unit_price));
      const rate = window.prompt(t("p2p.prompt.taxRate", { description: item.description }), "0.10")?.trim();
      return unit_price >= 0 ? [{ purchase_order_item_id: item.id, quantity: String(quantity), unit_price: String(unit_price), ...(rate ? { tax_rate: rate } : {}) }] : [];
    });
    if (!items.length) return;
    const tax = window.prompt(t("p2p.prompt.taxStated"), "0")?.trim() || "0";
    const registration_number = window.prompt(t("p2p.prompt.registration"))?.trim() || null;
    invoice.mutate({ id: o.id, invoice_number, items, tax, registration_number });
  };

  return (
    <>
      <PageHeader title={t("p2p.title")} subtitle={t("p2p.subtitle")} />
      <ErrorNotice error={error} />
      {notice && <p className="mb-3 rounded-md bg-surface-2 px-3 py-2 text-sm">{notice}</p>}
      {requests.isLoading || orders.isLoading || invoices.isLoading || payments.isLoading ? <Spinner label={t("p2p.loading")} /> : null}
      {requests.isError || orders.isError || invoices.isError || payments.isError ? <ErrorNotice error={requests.error ?? orders.error ?? invoices.error ?? payments.error} /> : null}
      <div className="grid gap-5 xl:grid-cols-2">
        <Card title={t("p2p.ready.title")}>
          {!requests.data?.items.length ? (
            <EmptyState title={t("p2p.ready.emptyTitle")}>{t("p2p.ready.emptyBody")}</EmptyState>
          ) : (
            <Table>
              <thead><tr><Th>{t("requests.col.request")}</Th><Th>{t("common.value")}</Th><Th /></tr></thead>
              <tbody>
                {requests.data.items.filter((r) => r.preferred_vendor_id).map((r) => (
                  <tr key={r.id}>
                    <Td><p className="font-medium">{r.number}</p><p className="text-xs text-muted">{r.title}</p></Td>
                    <Td>{money(r.estimated_total, r.currency)}</Td>
                    <Td>{can("vendor:manage") && <Button size="sm" loading={create.isPending} onClick={() => create.mutate(r.id)}>{t("p2p.ready.createPo")}</Button>}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <Card title={t("p2p.orders.title")}>
          {!orders.data?.length ? (
            <EmptyState title={t("p2p.orders.emptyTitle")}>{t("p2p.orders.emptyBody")}</EmptyState>
          ) : (
            <Table>
              <thead><tr><Th>{t("p2p.orders.po")}</Th><Th>{t("vendors.col.vendor")}</Th><Th>{t("common.value")}</Th><Th>{t("p2p.orders.state")}</Th><Th>{t("p2p.orders.action")}</Th></tr></thead>
              <tbody>
                {orders.data.map((o) => (
                  <tr key={o.id}>
                    <Td>
                      <span className="font-medium">{o.number}</span>
                      <p className="text-xs text-muted">{o.items.map((item) => `${item.description} × ${item.quantity}`).join(" · ")}</p>
                      <p className="text-xs text-muted">{date(o.created_at)}{o.awarded_quote_id ? ` · ${t("p2p.orders.awardedQuote")}` : ""}</p>
                    </Td>
                    <Td className="font-mono text-xs">{o.vendor_id.slice(0, 8)}</Td>
                    <Td>{money(o.total, o.currency)}</Td>
                    <Td>{labels.poStatus(o.status)}</Td>
                    <Td>
                      {can("vendor:manage") && (
                        <div className="flex flex-wrap gap-2">
                          {o.status === "approved" && <Button size="sm" variant="secondary" onClick={() => transition.mutate({ id: o.id, status: "sent" })}>{t("p2p.orders.markSent")}</Button>}
                          {o.status === "sent" && <Button size="sm" variant="secondary" onClick={() => transition.mutate({ id: o.id, status: "acknowledged" })}>{t("p2p.orders.acknowledge")}</Button>}
                          {["acknowledged", "partially_received"].includes(o.status) && <Button size="sm" variant="secondary" onClick={() => receiveLine(o)}>{t("p2p.orders.receiveLine")}</Button>}
                          {["partially_received", "received"].includes(o.status) && <Button size="sm" variant="secondary" onClick={() => addInvoice(o)}>{t("p2p.orders.addInvoice")}</Button>}
                        </div>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <Card title={t("p2p.invoices.title")} className="xl:col-span-2">
          {!invoices.data?.length ? (
            <EmptyState title={t("p2p.invoices.emptyTitle")}>{t("p2p.invoices.emptyBody")}</EmptyState>
          ) : (
            <Table>
              <thead><tr><Th>{t("p2p.invoices.invoice")}</Th><Th>{t("p2p.orders.po")}</Th><Th>{t("common.date")}</Th><Th>{t("common.amount")}</Th><Th>{t("p2p.invoices.match")}</Th><Th>{t("p2p.invoices.lineChecks")}</Th><Th /></tr></thead>
              <tbody>
                {invoices.data.map((i) => (
                  <tr key={i.id}>
                    <Td className="font-medium">{i.invoice_number}</Td>
                    <Td className="font-mono text-xs">{i.purchase_order_id.slice(0, 8)}</Td>
                    <Td>{date(i.invoice_date)}</Td>
                    <Td>{money(i.total, i.currency)}</Td>
                    <Td><span className="rounded-full bg-surface-2 px-2 py-1 text-xs">{labels.matchStatus(i.match_status)}</span></Td>
                    <Td className="text-xs text-muted">
                      <p>{i.line_match_details.map((line) => `L${line.line_no}: ${humanize(line.price_status)}, ${humanize(line.quantity_status)}`).join(" · ") || "—"}</p>
                      {i.match_details.qualified_invoice && i.match_details.qualified_invoice.status !== "not_applicable" && (
                        <p>
                          {t("p2p.invoices.registration", { status: humanize(i.match_details.qualified_invoice.status) })}
                          {i.match_details.tax?.status && i.match_details.tax.status !== "not_compared" ? ` · ${t("p2p.invoices.tax", { status: humanize(i.match_details.tax.status) })}` : ""}
                        </p>
                      )}
                      {i.match_details.exceptions?.map((reason) => <p key={reason} className="text-danger">{reason}</p>)}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-2">
                        {can("payment:approve") && ["mismatch", "partially_matched", "requires_review"].includes(i.match_status) && (
                          <>
                            <Button size="sm" variant="secondary" loading={decideInvoiceException.isPending} onClick={() => decideInvoiceException.mutate({ id: i.id, decision: "approve" })}>{t("p2p.invoices.approveException")}</Button>
                            <Button size="sm" variant="secondary" loading={decideInvoiceException.isPending} onClick={() => decideInvoiceException.mutate({ id: i.id, decision: "reject" })}>{t("p2p.invoices.rejectException")}</Button>
                          </>
                        )}
                        {can("purchase_request:read_all") && ["matched", "exception_approved"].includes(i.match_status) && !payments.data?.some((p) => p.invoice_id === i.id) && (
                          <Button size="sm" variant="secondary" onClick={() => requestPayment.mutate(i.id)}>{t("p2p.invoices.requestPayment")}</Button>
                        )}
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <Card title={t("p2p.payments.title")} className="xl:col-span-2">
          {can("payment:approve") && payments.data?.some((p) => p.status === "approved") && (
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <Button
                size="sm"
                loading={exportTransfers.isPending}
                onClick={() => {
                  const next = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
                  const day = window.prompt(t("p2p.prompt.transferDate"), next)?.trim();
                  if (day) exportTransfers.mutate(day);
                }}
              >
                {t("p2p.payments.export")}
              </Button>
              <span className="text-xs text-muted">{t("p2p.payments.exportHint")}</span>
            </div>
          )}
          {!payments.data?.length ? (
            <EmptyState title={t("p2p.payments.emptyTitle")}>{t("p2p.payments.emptyBody")}</EmptyState>
          ) : (
            <Table>
              <thead><tr><Th>{t("p2p.invoices.invoice")}</Th><Th>{t("common.value")}</Th><Th>{t("p2p.orders.state")}</Th><Th>{t("p2p.payments.decision")}</Th></tr></thead>
              <tbody>
                {payments.data.map((p) => (
                  <tr key={p.id}>
                    <Td className="font-mono text-xs">{p.invoice_id.slice(0, 8)}</Td>
                    <Td>{money(p.amount, p.currency)}</Td>
                    <Td>{labels.paymentStatus(p.status)}{p.export_reference && <p className="text-xs text-muted">{p.export_reference}</p>}</Td>
                    <Td>{can("payment:approve") && p.status === "pending_approval" ? <Button size="sm" loading={decidePayment.isPending} onClick={() => decidePayment.mutate(p.id)}>{t("p2p.payments.approve")}</Button> : (p.comment ?? "—")}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <QuoteSourcing canManage={can("vendor:manage")} />
        {can("analytics:read") && <DecisionSupport />}
        {can("vendor:manage") && <InvoiceOCR />}
      </div>
    </>
  );
}

type AwardContext = { followed_recommendation: boolean; override_reason: string | null; awarded_rank: number | null };
type Quote = { id: string; vendor_id: string; vendor_name: string; amount: string; currency: string; delivery_days: number; quality_score: string; contract_compliant: boolean; is_awarded: boolean; award_context: AwardContext | null; eligible: boolean; rank: number | null; score: number | null; reasons: string[] };
type VendorOption = { id: string; name: string };

function QuoteSourcing({ canManage }: { canManage: boolean }) {
  const { t } = useTranslation();
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
    onSuccess: () => {
      setAmount("");
      void qc.invalidateQueries({ queryKey: ["quotes", requestId] });
    },
  });
  const award = useMutation({
    mutationFn: ({ quoteId, override_reason }: { quoteId: string; override_reason?: string }) => api.post(`/purchase-requests/${requestId}/quotes/${quoteId}/award`, override_reason ? { override_reason } : undefined),
    onSuccess: () => {
      for (const key of ["quotes", "approved-requests", "sourcing-requests", "decision-support"]) void qc.invalidateQueries({ queryKey: key === "quotes" ? [key, requestId] : [key] });
    },
  });
  const selectedRequest = pending.data?.items.find((request) => request.id === requestId);
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (requestId && vendorId && Number(amount) >= 0) record.mutate();
  };
  const onAward = (quote: Quote) => {
    if (quote.id === quotes.data?.recommended_quote_id) {
      award.mutate({ quoteId: quote.id });
      return;
    }
    const reason = window.prompt(t("p2p.quotes.overridePrompt"))?.trim();
    if (reason) award.mutate({ quoteId: quote.id, override_reason: reason });
  };
  return (
    <Card title={t("p2p.quotes.title")} className="xl:col-span-2">
      <p className="mb-4 text-sm text-muted">{t("p2p.quotes.intro")}</p>
      {canManage && (
        <div className="mb-4 grid gap-3 md:grid-cols-3">
          <Field label={t("p2p.quotes.request")}>
            <Select value={requestId} onChange={(e) => setRequestId(e.target.value)}>
              <option value="">{t("p2p.quotes.chooseRequest")}</option>
              {pending.data?.items.map((r) => <option key={r.id} value={r.id}>{r.number} · {r.title}</option>)}
            </Select>
          </Field>
          <Field label={t("p2p.quotes.vendor")}>
            <Select value={vendorId} onChange={(e) => setVendorId(e.target.value)}>
              <option value="">{t("p2p.quotes.chooseVendor")}</option>
              {vendors.data?.items.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </Select>
          </Field>
          <form className="flex items-end gap-2" onSubmit={onSubmit}>
            <Field label={t("p2p.quotes.amount")}><Input type="number" min="0" step="0.01" required value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
            <Button size="sm" loading={record.isPending} disabled={!requestId || !vendorId}>{t("p2p.quotes.record")}</Button>
          </form>
          <Field label={t("p2p.quotes.deliveryDays")}><Input type="number" min="0" value={delivery} onChange={(e) => setDelivery(e.target.value)} /></Field>
          <Field label={t("p2p.quotes.quality")}><Input type="number" min="0" max="5" step="0.1" value={quality} onChange={(e) => setQuality(e.target.value)} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={contract} onChange={(e) => setContract(e.target.checked)} /> {t("p2p.quotes.contract")}</label>
        </div>
      )}
      {record.isError && <ErrorNotice error={record.error} />}
      {!requestId ? (
        <EmptyState title={t("p2p.quotes.selectTitle")}>{t("p2p.quotes.selectBody")}</EmptyState>
      ) : quotes.isLoading ? (
        <Spinner label={t("p2p.quotes.comparing")} />
      ) : quotes.isError ? (
        <ErrorNotice error={quotes.error} />
      ) : !quotes.data?.quotes.length ? (
        <EmptyState title={t("p2p.quotes.none")} />
      ) : (
        <>
          <Table>
            <thead><tr><Th>{t("p2p.quotes.rank")}</Th><Th>{t("vendors.col.vendor")}</Th><Th>{t("p2p.quotes.quote")}</Th><Th>{t("p2p.quotes.delivery")}</Th><Th>{t("p2p.quotes.score")}</Th><Th>{t("p2p.quotes.evidence")}</Th><Th /></tr></thead>
            <tbody>
              {quotes.data.quotes.map((q) => (
                <tr key={q.id} className={q.eligible ? "" : "opacity-60"}>
                  <Td>{q.rank ?? "—"}{q.id === quotes.data.recommended_quote_id && <p className="text-xs font-medium text-primary">{t("p2p.quotes.recommended")}</p>}</Td>
                  <Td className="font-medium">{q.vendor_name}</Td>
                  <Td>{money(q.amount, q.currency)}</Td>
                  <Td>{t("p2p.quotes.days", { count: q.delivery_days })}</Td>
                  <Td>{q.score === null ? t("p2p.quotes.ineligible") : t("p2p.quotes.scoreOf", { score: q.score.toFixed(1) })}</Td>
                  <Td className="text-xs text-muted">
                    {q.reasons.join(" · ")}
                    {q.award_context && <p>{q.award_context.followed_recommendation ? t("p2p.quotes.awardedAsRecommended") : t("p2p.quotes.override", { reason: q.award_context.override_reason ?? "" })}</p>}
                  </Td>
                  <Td>
                    {canManage && selectedRequest?.status === "approved" && (
                      <Button size="sm" variant="secondary" loading={award.isPending} disabled={!q.eligible || q.is_awarded || quotes.data.quotes.some((row) => row.is_awarded)} onClick={() => onAward(q)}>
                        {q.is_awarded ? t("p2p.quotes.awarded") : t("p2p.quotes.award")}
                      </Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
          {award.isError && <ErrorNotice error={award.error} />}
        </>
      )}
    </Card>
  );
}

type OCRResult = { status: string; filename: string; extracted_fields: Record<string, string | null>; raw_text: string; source: string };

function InvoiceOCR() {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const extraction = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error(t("p2p.ocr.selectFirst"));
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      return api.post<OCRResult>("/procurement-assistant/invoice-ocr", { filename: file.name, content_base64: btoa(binary) });
    },
  });
  return (
    <Card title={t("p2p.ocr.title")} className="xl:col-span-2">
      <p className="mb-3 text-sm text-muted">{t("p2p.ocr.intro")}</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          {t("p2p.ocr.file")}
          <input type="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" onChange={(event) => setFile(event.target.files?.[0] ?? null)} className="mt-1 block text-sm" />
        </label>
        <Button loading={extraction.isPending} disabled={!file} onClick={() => extraction.mutate()}>{t("p2p.ocr.extract")}</Button>
      </div>
      <ErrorNotice error={extraction.error} />
      {extraction.data && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card title={t("p2p.ocr.fields")}>
            <dl className="space-y-2 text-sm">
              {Object.entries(extraction.data.extracted_fields).map(([key, value]) => (
                <div key={key} className="flex justify-between gap-3"><dt className="text-muted">{humanize(key)}</dt><dd className="text-right font-medium">{value ?? t("p2p.ocr.notExtracted")}</dd></div>
              ))}
            </dl>
          </Card>
          <Card title={t("p2p.ocr.text")}><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{extraction.data.raw_text}</pre></Card>
        </div>
      )}
    </Card>
  );
}

type OutcomeBucket = { awards: number; delivered: number; on_time: number; late: number; pending: number; on_time_rate: number | null; invoiced: number; first_pass_invoice_match_rate: number | null; damaged_quantity_rate: number | null };
type SourcingDecisions = { data_scope: string; awards_with_recommendation: number; followed: number; overridden: number; agreement_rate: number | null; outcomes: Record<"followed" | "overridden", OutcomeBucket>; overrides: { request_number: string; awarded_vendor: string; recommended_vendor: string | null; override_reason: string | null; delivery_outcome: string }[]; interpretation: string };

const pct = (value: number | null) => (value === null ? "—" : percent(value, 0));

function DecisionSupport() {
  const { t } = useTranslation();
  const report = useQuery({ queryKey: ["decision-support"], queryFn: () => api.get<SourcingDecisions>("/decision-support/sourcing") });
  return (
    <Card title={t("p2p.decisions.title")} className="xl:col-span-2">
      {report.isLoading ? (
        <Spinner label={t("p2p.decisions.loading")} />
      ) : report.isError ? (
        <ErrorNotice error={report.error} />
      ) : !report.data?.awards_with_recommendation ? (
        <EmptyState title={t("p2p.decisions.emptyTitle")}>{t("p2p.decisions.emptyBody")}</EmptyState>
      ) : (
        <>
          <p className="mb-3 text-sm text-muted">{report.data.data_scope}</p>
          <div className="mb-4 grid gap-3 sm:grid-cols-3">
            <div><p className="text-xs text-muted">{t("p2p.decisions.withRecommendation")}</p><p className="text-xl font-semibold">{report.data.awards_with_recommendation}</p></div>
            <div><p className="text-xs text-muted">{t("p2p.decisions.followedOverridden")}</p><p className="text-xl font-semibold">{report.data.followed} / {report.data.overridden}</p></div>
            <div><p className="text-xs text-muted">{t("p2p.decisions.agreement")}</p><p className="text-xl font-semibold">{pct(report.data.agreement_rate)}</p></div>
          </div>
          <Table>
            <thead><tr><Th>{t("p2p.decisions.decision")}</Th><Th>{t("p2p.decisions.awards")}</Th><Th>{t("p2p.decisions.onTime")}</Th><Th>{t("p2p.decisions.late")}</Th><Th>{t("p2p.decisions.pending")}</Th><Th>{t("p2p.decisions.firstInvoice")}</Th><Th>{t("p2p.decisions.damaged")}</Th></tr></thead>
            <tbody>
              {(["followed", "overridden"] as const).map((key) => {
                const b = report.data.outcomes[key];
                return (
                  <tr key={key}>
                    <Td className="font-medium">{t(`p2p.decisions.${key}`)}</Td>
                    <Td>{b.awards}</Td>
                    <Td>{b.on_time} ({pct(b.on_time_rate)})</Td>
                    <Td>{b.late}</Td>
                    <Td>{b.pending}</Td>
                    <Td>{pct(b.first_pass_invoice_match_rate)}</Td>
                    <Td>{pct(b.damaged_quantity_rate)}</Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          {report.data.overrides.length > 0 && (
            <div className="mt-4 space-y-2 text-sm">
              {report.data.overrides.map((o) => (
                <p key={o.request_number}>
                  <span className="font-medium">{o.request_number}</span>: {t("p2p.decisions.overrideLine", { awarded: o.awarded_vendor, recommended: o.recommended_vendor ?? t("p2p.decisions.theRecommendation"), reason: o.override_reason ?? "", outcome: humanize(o.delivery_outcome) })}
                </p>
              ))}
            </div>
          )}
          <p className="mt-3 text-xs text-muted">{report.data.interpretation}</p>
        </>
      )}
    </Card>
  );
}
