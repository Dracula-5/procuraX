import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/useAuth";
import { ContractBadge, RiskBadge, VendorStatusBadge } from "../components/badges";
import { Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { CATEGORIES, date, label } from "../lib/format";
import type { Page, Vendor } from "../lib/types";

const STATUSES = ["approved", "pending_review", "suspended", "blocked"];

export function Vendors() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [filters, setFilters] = useState({ q: "", category: "", status: "" });
  const [creating, setCreating] = useState(false);

  const vendors = useQuery({
    queryKey: ["vendors", filters],
    queryFn: () => api.get<Page<Vendor>>("/vendors", { ...filters, limit: 200 }),
    placeholderData: keepPreviousData,
  });
  const changeStatus = useMutation({
    mutationFn: ({ id, status, reason }: { id: string; status: string; reason?: string }) =>
      api.post<Vendor>(`/vendors/${id}/status`, { status, reason }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vendors"] }),
  });

  const onStatus = (v: Vendor, status: string) => {
    let reason: string | undefined;
    if (status === "blocked" || status === "suspended") {
      reason = window.prompt(`Reason for marking ${v.name} as ${status}?`) ?? undefined;
      if (!reason) return;
    }
    changeStatus.mutate({ id: v.id, status, reason });
  };

  return (
    <>
      <PageHeader
        title="Vendors"
        subtitle="Vendor master. New vendors start in onboarding review; approval is a separate, permissioned action."
        actions={
          can("vendor:manage") && (
            <Button onClick={() => setCreating((c) => !c)}>
              <Plus className="size-4" /> Onboard vendor
            </Button>
          )
        }
      />
      {creating && <VendorForm onDone={() => setCreating(false)} />}
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Input placeholder="Search name" value={filters.q} onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))} aria-label="Search" />
        <Select value={filters.category} onChange={(e) => setFilters((f) => ({ ...f, category: e.target.value }))} aria-label="Category">
          <option value="">All categories</option>
          {Object.entries(CATEGORIES).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </Select>
        <Select value={filters.status} onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))} aria-label="Status">
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace("_", " ")}
            </option>
          ))}
        </Select>
      </div>
      <ErrorNotice error={vendors.error ?? changeStatus.error} />
      {vendors.isLoading ? (
        <Spinner />
      ) : !vendors.data?.items.length ? (
        <EmptyState title="No vendors found" />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Vendor</Th>
              <Th>Categories</Th>
              <Th>Status</Th>
              <Th>Onboarded</Th>
              {can("vendor:approve") && <Th>Change status</Th>}
            </tr>
          </thead>
          <tbody>
            {vendors.data.items.map((v) => (
              <tr key={v.id}>
                <Td>
                  <p className="font-medium">{v.name}</p>
                  <p className="text-xs text-muted">
                    {v.invoice_registration_number ?? "No invoice registration no."} · {v.payment_terms_days}-day terms
                  </p>
                </Td>
                <Td className="text-xs">{v.categories.map((c) => label(CATEGORIES, c)).join(", ")}</Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    <VendorStatusBadge status={v.status} />
                    <RiskBadge level={v.risk_level} />
                    <ContractBadge status={v.contract_status} />
                  </div>
                </Td>
                <Td className="whitespace-nowrap text-muted">{date(v.created_at)}</Td>
                {can("vendor:approve") && (
                  <Td>
                    <Select
                      className="h-8 w-36"
                      value=""
                      onChange={(e) => e.target.value && onStatus(v, e.target.value)}
                      aria-label={`Change status of ${v.name}`}
                    >
                      <option value="">Set…</option>
                      {STATUSES.filter((s) => s !== v.status).map((s) => (
                        <option key={s} value={s}>
                          {s.replace("_", " ")}
                        </option>
                      ))}
                    </Select>
                  </Td>
                )}
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}

function VendorForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    name: "",
    categories: [] as string[],
    invoice_registration_number: "",
    risk_level: "unknown",
    contract_status: "none",
    contact_email: "",
    payment_terms_days: 30,
  });
  const create = useMutation({
    mutationFn: () =>
      api.post<Vendor>("/vendors", {
        ...form,
        invoice_registration_number: form.invoice_registration_number || null,
        contact_email: form.contact_email || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["vendors"] });
      onDone();
    },
  });
  const errors = create.error instanceof ApiError ? create.error.fieldErrors() : {};
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  const toggle = (c: string) =>
    setForm((f) => ({ ...f, categories: f.categories.includes(c) ? f.categories.filter((x) => x !== c) : [...f.categories, c] }));

  return (
    <Card title="Onboard a vendor" className="mb-6">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" error={errors.name}>
          <Input required value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        </Field>
        <Field label="Invoice registration number" hint="Japan Qualified Invoice System: T + 13 digits" error={errors.invoice_registration_number}>
          <Input value={form.invoice_registration_number} placeholder="T1234567890123" onChange={(e) => setForm((f) => ({ ...f, invoice_registration_number: e.target.value }))} />
        </Field>
        <Field label="Risk level">
          <Select value={form.risk_level} onChange={(e) => setForm((f) => ({ ...f, risk_level: e.target.value }))}>
            {["unknown", "low", "medium", "high"].map((r) => (
              <option key={r}>{r}</option>
            ))}
          </Select>
        </Field>
        <Field label="Contract">
          <Select value={form.contract_status} onChange={(e) => setForm((f) => ({ ...f, contract_status: e.target.value }))}>
            {["none", "active", "expired"].map((r) => (
              <option key={r}>{r}</option>
            ))}
          </Select>
        </Field>
        <Field label="Contact e-mail" error={errors.contact_email}>
          <Input type="email" value={form.contact_email} onChange={(e) => setForm((f) => ({ ...f, contact_email: e.target.value }))} />
        </Field>
        <Field label="Payment terms (days)">
          <Input type="number" min={0} max={365} value={form.payment_terms_days} onChange={(e) => setForm((f) => ({ ...f, payment_terms_days: Number(e.target.value) }))} />
        </Field>
        <fieldset className="sm:col-span-2">
          <legend className="text-sm font-medium">Categories supplied</legend>
          {errors.categories && <p className="text-xs text-danger">{errors.categories}</p>}
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(CATEGORIES).map(([k, v]) => (
              <label key={k} className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-sm">
                <input type="checkbox" checked={form.categories.includes(k)} onChange={() => toggle(k)} className="accent-[var(--primary)]" />
                {v}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="sm:col-span-2">
          {!Object.keys(errors).length && <ErrorNotice error={create.error} />}
          <div className="mt-2 flex gap-2">
            <Button type="submit" loading={create.isPending} disabled={!form.categories.length}>
              Create (pending review)
            </Button>
            <Button type="button" variant="ghost" onClick={onDone}>
              Cancel
            </Button>
          </div>
        </div>
      </form>
    </Card>
  );
}

