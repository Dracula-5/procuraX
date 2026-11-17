import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Button, Card, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Textarea } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { money } from "../lib/format";
import { CATEGORY_KEYS, useLabels } from "../lib/labels";
import type { CostCenter, Department, Page, PRDetail, RatesResponse, Vendor } from "../lib/types";

interface Line {
  description: string;
  quantity: string;
  unit_price: string;
  uom: string;
}

const emptyLine = (): Line => ({ description: "", quantity: "1", unit_price: "", uom: "EA" });

type FormState = {
  title: string;
  category: string;
  cost_center_id: string;
  preferred_vendor_id: string;
  required_by: string;
  justification: string;
  is_emergency: boolean;
  currency: string;
};

/** Loads the draft (when editing), then mounts the form with its initial values. */
export function RequestForm() {
  const { t } = useTranslation();
  const { id } = useParams();
  const existing = useQuery({
    queryKey: ["request", id],
    queryFn: () => api.get<PRDetail>(`/purchase-requests/${id}`),
    enabled: Boolean(id),
  });
  if (id && existing.isLoading) return <Spinner />;
  if (id && !existing.data) return <ErrorNotice error={existing.error} />;
  if (existing.data && existing.data.status !== "draft") {
    return <ErrorNotice error={new Error(t("requestForm.onlyDrafts"))} />;
  }
  return <RequestFormBody key={id ?? "new"} existing={existing.data ?? null} />;
}

function RequestFormBody({ existing }: { existing: PRDetail | null }) {
  const { t } = useTranslation();
  const labels = useLabels();
  const editing = existing !== null;
  const id = existing?.id;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { me, can } = useAuth();
  const baseCurrency = me?.organization.base_currency ?? "JPY";
  const rates = useQuery({ queryKey: ["fx-rates"], queryFn: () => api.get<RatesResponse>("/fx/rates") });

  const costCenters = useQuery({ queryKey: ["cost-centers"], queryFn: () => api.get<CostCenter[]>("/cost-centers") });
  const departments = useQuery({ queryKey: ["departments"], queryFn: () => api.get<Department[]>("/departments") });
  const vendors = useQuery({
    queryKey: ["vendors", "all"],
    queryFn: () => api.get<Page<Vendor>>("/vendors", { limit: 200 }),
    enabled: can("vendor:read"),
  });

  const [form, setForm] = useState<FormState>(() => ({
    title: existing?.title ?? "",
    category: existing?.category ?? "office_supplies",
    cost_center_id: existing?.cost_center_id ?? "",
    preferred_vendor_id: existing?.preferred_vendor_id ?? "",
    required_by: existing?.required_by ?? "",
    justification: existing?.justification ?? "",
    is_emergency: existing?.is_emergency ?? false,
    currency: existing?.currency ?? baseCurrency,
  }));
  const currency = form.currency;
  const currencies = [baseCurrency, ...(rates.data?.supported_currencies ?? []).filter((c) => c !== baseCurrency)];
  const [lines, setLines] = useState<Line[]>(() =>
    existing?.items.length
      ? existing.items.map((i) => ({ description: i.description, quantity: i.quantity, unit_price: i.unit_price, uom: i.uom }))
      : [emptyLine()],
  );

  // New requests default to a cost centre in the requester's own department (derived, not stored).
  const defaultCostCenter =
    costCenters.data?.find((c) => c.is_active && c.department_id === me?.department_id) ?? costCenters.data?.find((c) => c.is_active);
  const costCenterId = form.cost_center_id || (editing ? "" : (defaultCostCenter?.id ?? ""));

  const total = useMemo(
    () => lines.reduce((sum, l) => sum + (Number(l.quantity) || 0) * (Number(l.unit_price) || 0), 0),
    [lines],
  );
  const deptName = (deptId: string) => departments.data?.find((d) => d.id === deptId)?.code ?? "";

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        ...form,
        cost_center_id: costCenterId || null,
        preferred_vendor_id: form.preferred_vendor_id || null,
        required_by: form.required_by || null,
        items: lines
          .filter((l) => l.description.trim() || l.unit_price)
          .map((l) => ({ ...l, description: l.description.trim() })),
      };
      return editing ? api.patch<PRDetail>(`/purchase-requests/${id}`, body) : api.post<PRDetail>("/purchase-requests", body);
    },
    onSuccess: (pr) => {
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.setQueryData(["request", pr.id], pr);
      navigate(`/app/requests/${pr.id}`);
    },
  });
  const fieldErrors = save.error instanceof ApiError ? save.error.fieldErrors() : {};

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value }));
  const setLine = (i: number, k: keyof Line, v: string) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, [k]: v } : l)));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <>
      <PageHeader
        title={editing ? t("requestForm.editTitle", { number: existing?.number }) : t("requestForm.newTitle")}
        subtitle={t("requestForm.subtitle")}
      />
      <form onSubmit={submit} className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          <Card title={t("requestForm.whatTitle")}>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <Field label={t("requestForm.title")} error={fieldErrors.title}>
                  <Input required minLength={3} value={form.title} onChange={set("title")} placeholder={t("requestForm.titlePlaceholder")} />
                </Field>
              </div>
              <Field label={t("common.category")}>
                <Select value={form.category} onChange={set("category")}>
                  {CATEGORY_KEYS.map((k) => (
                    <option key={k} value={k}>
                      {labels.category(k)}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={t("requestForm.neededBy")}>
                <Input type="date" value={form.required_by} onChange={set("required_by")} />
              </Field>
              <Field label={t("requestForm.currency")} hint={currency !== baseCurrency ? t("requestForm.currencyHint", { base: baseCurrency }) : undefined}>
                <Select value={currency} onChange={set("currency")}>
                  {currencies.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={t("requestForm.costCentre")} hint={t("requestForm.costCentreHint")}>
                <Select value={costCenterId} onChange={set("cost_center_id")}>
                  <option value="">{t("common.selectPlaceholder")}</option>
                  {costCenters.data
                    ?.filter((c) => c.is_active)
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.code} · {c.name} {deptName(c.department_id) && `(${deptName(c.department_id)})`}
                      </option>
                    ))}
                </Select>
              </Field>
              <Field label={t("requestForm.preferredVendor")} hint={t("requestForm.preferredVendorHint")}>
                <Select value={form.preferred_vendor_id} onChange={set("preferred_vendor_id")}>
                  <option value="">{t("requestForm.noVendor")}</option>
                  {vendors.data?.items.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name} · {labels.vendorStatus(v.status)}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          </Card>

          <Card
            title={t("requestForm.lines")}
            actions={
              <Button type="button" variant="secondary" size="sm" onClick={() => setLines((l) => [...l, emptyLine()])} disabled={lines.length >= 100}>
                <Plus className="size-4" /> {t("requestForm.addLine")}
              </Button>
            }
          >
            <div className="space-y-3">
              {lines.map((line, i) => (
                <div key={i} className="grid grid-cols-2 gap-2 sm:grid-cols-[1fr_90px_140px_70px_36px]">
                  <Input className="col-span-2 sm:col-span-1" placeholder={t("requestForm.description")} value={line.description} onChange={(e) => setLine(i, "description", e.target.value)} aria-label={t("requestForm.lineDescription", { n: i + 1 })} />
                  <Input type="number" min="0.001" step="any" value={line.quantity} onChange={(e) => setLine(i, "quantity", e.target.value)} aria-label={t("common.quantity")} />
                  <Input type="number" min="0" step="any" placeholder={t("requestForm.unitPriceIn", { currency })} value={line.unit_price} onChange={(e) => setLine(i, "unit_price", e.target.value)} aria-label={t("common.unitPrice")} />
                  <Input value={line.uom} onChange={(e) => setLine(i, "uom", e.target.value)} aria-label={t("requestForm.unit")} />
                  <Button type="button" variant="ghost" size="sm" className="h-10" onClick={() => setLines((ls) => (ls.length > 1 ? ls.filter((_, j) => j !== i) : [emptyLine()]))} aria-label={t("requestForm.removeLine")}>
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          </Card>

          <Card title={t("requestForm.justificationTitle")}>
            <Field label={t("requestForm.justification")} error={fieldErrors.justification}>
              <Textarea value={form.justification} onChange={set("justification")} />
            </Field>
            <label className="mt-4 flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-0.5 size-4 accent-[var(--primary)]" checked={form.is_emergency} onChange={set("is_emergency")} />
              <span>
                <span className="font-medium">{t("requestForm.emergency")}</span>
                <span className="block text-muted">{t("requestForm.emergencyHint")}</span>
              </span>
            </label>
          </Card>
        </div>

        <div className="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <Card title={t("requestForm.summary")}>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-muted">{t("requestForm.lineCount")}</dt>
                <dd>{lines.filter((l) => l.description.trim()).length}</dd>
              </div>
              <div className="flex justify-between text-base font-semibold">
                <dt>{t("requestForm.estimatedTotal")}</dt>
                <dd className="tabular">{money(total, currency)}</dd>
              </div>
            </dl>
            <p className="mt-3 text-xs text-muted">{t("requestForm.rounding", { currency })}</p>
          </Card>
          {!Object.keys(fieldErrors).length && <ErrorNotice error={save.error} />}
          <Button type="submit" className="w-full" loading={save.isPending}>
            {editing ? t("common.saveChanges") : t("requestForm.saveDraft")}
          </Button>
          <Button type="button" variant="ghost" className="w-full" onClick={() => navigate(-1)}>
            {t("common.cancel")}
          </Button>
        </div>
      </form>
    </>
  );
}
