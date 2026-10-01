import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Button, Card, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Textarea } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { CATEGORIES, money } from "../lib/format";
import type { CostCenter, Department, Page, PRDetail, Vendor } from "../lib/types";

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
};

/** Loads the draft (when editing), then mounts the form with its initial values. */
export function RequestForm() {
  const { id } = useParams();
  const existing = useQuery({
    queryKey: ["request", id],
    queryFn: () => api.get<PRDetail>(`/purchase-requests/${id}`),
    enabled: Boolean(id),
  });
  if (id && existing.isLoading) return <Spinner />;
  if (id && !existing.data) return <ErrorNotice error={existing.error} />;
  if (existing.data && existing.data.status !== "draft") {
    return <ErrorNotice error={new Error("Only draft requests can be edited. Reopen the request first.")} />;
  }
  return <RequestFormBody key={id ?? "new"} existing={existing.data ?? null} />;
}

function RequestFormBody({ existing }: { existing: PRDetail | null }) {
  const editing = existing !== null;
  const id = existing?.id;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { me, can } = useAuth();
  const currency = me?.organization.base_currency ?? "JPY";

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
  }));
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
        title={editing ? `Edit ${existing?.number}` : "New purchase request"}
        subtitle="Saved as a draft. On submission the policy engine decides the approval route — you can preview it first."
      />
      <form onSubmit={submit} className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          <Card title="What do you need?">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <Field label="Title" error={fieldErrors.title}>
                  <Input required minLength={3} value={form.title} onChange={set("title")} placeholder="e.g. Laptops for new joiners" />
                </Field>
              </div>
              <Field label="Category">
                <Select value={form.category} onChange={set("category")}>
                  {Object.entries(CATEGORIES).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Needed by">
                <Input type="date" value={form.required_by} onChange={set("required_by")} />
              </Field>
              <Field label="Cost centre" hint="The department that owns this cost centre approves and its budget is checked.">
                <Select value={costCenterId} onChange={set("cost_center_id")}>
                  <option value="">— Select —</option>
                  {costCenters.data
                    ?.filter((c) => c.is_active)
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.code} · {c.name} {deptName(c.department_id) && `(${deptName(c.department_id)})`}
                      </option>
                    ))}
                </Select>
              </Field>
              <Field label="Preferred vendor" hint="Optional. Blocked vendors are rejected by policy.">
                <Select value={form.preferred_vendor_id} onChange={set("preferred_vendor_id")}>
                  <option value="">— None / let procurement source —</option>
                  {vendors.data?.items.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name} · {v.status.replace("_", " ")}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          </Card>

          <Card
            title="Line items"
            actions={
              <Button type="button" variant="secondary" size="sm" onClick={() => setLines((l) => [...l, emptyLine()])} disabled={lines.length >= 100}>
                <Plus className="size-4" /> Add line
              </Button>
            }
          >
            <div className="space-y-3">
              {lines.map((line, i) => (
                <div key={i} className="grid grid-cols-2 gap-2 sm:grid-cols-[1fr_90px_140px_70px_36px]">
                  <Input className="col-span-2 sm:col-span-1" placeholder="Description" value={line.description} onChange={(e) => setLine(i, "description", e.target.value)} aria-label={`Line ${i + 1} description`} />
                  <Input type="number" min="0.001" step="any" value={line.quantity} onChange={(e) => setLine(i, "quantity", e.target.value)} aria-label="Quantity" />
                  <Input type="number" min="0" step="any" placeholder={`Unit price (${currency})`} value={line.unit_price} onChange={(e) => setLine(i, "unit_price", e.target.value)} aria-label="Unit price" />
                  <Input value={line.uom} onChange={(e) => setLine(i, "uom", e.target.value)} aria-label="Unit" />
                  <Button type="button" variant="ghost" size="sm" className="h-10" onClick={() => setLines((ls) => (ls.length > 1 ? ls.filter((_, j) => j !== i) : [emptyLine()]))} aria-label="Remove line">
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Business justification">
            <Field label="Why is this purchase needed?" error={fieldErrors.justification}>
              <Textarea value={form.justification} onChange={set("justification")} />
            </Field>
            <label className="mt-4 flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-0.5 size-4 accent-[var(--primary)]" checked={form.is_emergency} onChange={set("is_emergency")} />
              <span>
                <span className="font-medium">Emergency purchase</span>
                <span className="block text-muted">Skips line-manager approval, goes to the department head, and is reviewed retrospectively. Requires a written justification.</span>
              </span>
            </label>
          </Card>
        </div>

        <div className="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <Card title="Summary">
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-muted">Lines</dt>
                <dd>{lines.filter((l) => l.description.trim()).length}</dd>
              </div>
              <div className="flex justify-between text-base font-semibold">
                <dt>Estimated total</dt>
                <dd className="tabular">{money(total, currency)}</dd>
              </div>
            </dl>
            <p className="mt-3 text-xs text-muted">Final amounts are rounded to {currency} minor units by the server.</p>
          </Card>
          {!Object.keys(fieldErrors).length && <ErrorNotice error={save.error} />}
          <Button type="submit" className="w-full" loading={save.isPending}>
            {editing ? "Save changes" : "Save draft"}
          </Button>
          <Button type="button" variant="ghost" className="w-full" onClick={() => navigate(-1)}>
            Cancel
          </Button>
        </div>
      </form>
    </>
  );
}

