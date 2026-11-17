import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Landmark, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { ContractBadge, RiskBadge, VendorStatusBadge } from "../components/badges";
import { Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { date } from "../lib/format";
import { CATEGORY_KEYS, useLabels } from "../lib/labels";
import type { Page, Vendor } from "../lib/types";

const STATUSES = ["approved", "pending_review", "suspended", "blocked"];

export function Vendors() {
  const { t } = useTranslation();
  const labels = useLabels();
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
      reason = window.prompt(t("vendors.reasonPrompt", { name: v.name, status: labels.vendorStatus(status) })) ?? undefined;
      if (!reason) return;
    }
    changeStatus.mutate({ id: v.id, status, reason });
  };

  return (
    <>
      <PageHeader
        title={t("vendors.title")}
        subtitle={t("vendors.subtitle")}
        actions={
          can("vendor:manage") && (
            <Button onClick={() => setCreating((c) => !c)}>
              <Plus className="size-4" /> {t("vendors.onboard")}
            </Button>
          )
        }
      />
      {creating && <VendorForm onDone={() => setCreating(false)} />}
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Input placeholder={t("vendors.searchPlaceholder")} value={filters.q} onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))} aria-label={t("common.search")} />
        <Select value={filters.category} onChange={(e) => setFilters((f) => ({ ...f, category: e.target.value }))} aria-label={t("common.category")}>
          <option value="">{t("requests.allCategories")}</option>
          {CATEGORY_KEYS.map((k) => (
            <option key={k} value={k}>
              {labels.category(k)}
            </option>
          ))}
        </Select>
        <Select value={filters.status} onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))} aria-label={t("common.status")}>
          <option value="">{t("requests.allStatuses")}</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {labels.vendorStatus(s)}
            </option>
          ))}
        </Select>
      </div>
      <ErrorNotice error={vendors.error ?? changeStatus.error} />
      {vendors.isLoading ? (
        <Spinner />
      ) : !vendors.data?.items.length ? (
        <EmptyState title={t("vendors.empty")} />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t("vendors.col.vendor")}</Th>
              <Th>{t("vendors.col.categories")}</Th>
              <Th>{t("common.status")}</Th>
              <Th>{t("vendors.col.onboarded")}</Th>
              {can("vendor:approve") && <Th>{t("vendors.col.changeStatus")}</Th>}
            </tr>
          </thead>
          <tbody>
            {vendors.data.items.map((v) => (
              <tr key={v.id}>
                <Td>
                  <p className="font-medium">{v.name}</p>
                  <p className="text-xs text-muted">
                    {v.invoice_registration_number ?? t("vendors.noRegistration")} · {t("vendors.terms", { days: v.payment_terms_days })}
                  </p>
                  {can("vendor:manage") && (
                    <Link to={`/app/vendors/${v.id}/bank-account`} className="mt-1 inline-flex items-center gap-1 text-xs text-primary hover:underline">
                      <Landmark className="size-3" /> {t("vendors.bankAccount")}
                    </Link>
                  )}
                </Td>
                <Td className="text-xs">{v.categories.map((c) => labels.category(c)).join(t("common.listSeparator"))}</Td>
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
                      aria-label={t("vendors.changeStatusOf", { name: v.name })}
                    >
                      <option value="">{t("vendors.set")}</option>
                      {STATUSES.filter((s) => s !== v.status).map((s) => (
                        <option key={s} value={s}>
                          {labels.vendorStatus(s)}
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
  const { t } = useTranslation();
  const labels = useLabels();
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
    <Card title={t("vendors.form.title")} className="mb-6">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Field label={t("vendors.form.name")} error={errors.name}>
          <Input required value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        </Field>
        <Field label={t("vendors.form.registration")} hint={t("vendors.form.registrationHint")} error={errors.invoice_registration_number}>
          <Input value={form.invoice_registration_number} placeholder="T1234567890123" onChange={(e) => setForm((f) => ({ ...f, invoice_registration_number: e.target.value }))} />
        </Field>
        <Field label={t("vendors.form.risk")}>
          <Select value={form.risk_level} onChange={(e) => setForm((f) => ({ ...f, risk_level: e.target.value }))}>
            {["unknown", "low", "medium", "high"].map((r) => (
              <option key={r} value={r}>
                {labels.risk(r)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("vendors.form.contract")}>
          <Select value={form.contract_status} onChange={(e) => setForm((f) => ({ ...f, contract_status: e.target.value }))}>
            {["none", "active", "expired"].map((r) => (
              <option key={r} value={r}>
                {labels.contract(r)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("vendors.form.email")} error={errors.contact_email}>
          <Input type="email" value={form.contact_email} onChange={(e) => setForm((f) => ({ ...f, contact_email: e.target.value }))} />
        </Field>
        <Field label={t("vendors.form.terms")}>
          <Input type="number" min={0} max={365} value={form.payment_terms_days} onChange={(e) => setForm((f) => ({ ...f, payment_terms_days: Number(e.target.value) }))} />
        </Field>
        <fieldset className="sm:col-span-2">
          <legend className="text-sm font-medium">{t("vendors.form.categories")}</legend>
          {errors.categories && <p className="text-xs text-danger">{errors.categories}</p>}
          <div className="mt-2 flex flex-wrap gap-2">
            {CATEGORY_KEYS.map((k) => (
              <label key={k} className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-sm">
                <input type="checkbox" checked={form.categories.includes(k)} onChange={() => toggle(k)} className="accent-[var(--primary)]" />
                {labels.category(k)}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="sm:col-span-2">
          {!Object.keys(errors).length && <ErrorNotice error={create.error} />}
          <div className="mt-2 flex gap-2">
            <Button type="submit" loading={create.isPending} disabled={!form.categories.length}>
              {t("vendors.form.create")}
            </Button>
            <Button type="button" variant="ghost" onClick={onDone}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      </form>
    </Card>
  );
}
