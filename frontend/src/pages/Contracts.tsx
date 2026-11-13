import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Paperclip } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { Badge, Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Textarea, Th, type Tone } from "../components/ui";
import { api } from "../lib/api";
import { downloadStored, toBase64 } from "../lib/files";
import { date, money } from "../lib/format";
import { CATEGORY_KEYS, useLabels } from "../lib/labels";
import type { Contract, ContractState, Page, RatesResponse, Vendor } from "../lib/types";

const STATE_TONE: Record<ContractState, Tone> = { draft: "neutral", active: "ok", expired: "warn", terminated: "danger" };

type Draft = { vendor_id: string; title: string; categories: string[]; starts_on: string; ends_on: string; value_cap: string; currency: string };

/** Supplier contracts: draft → active → expired / terminated, with a value cap and the signed file. */
export function Contracts() {
  const { t } = useTranslation();
  const { me, can } = useAuth();
  const canManage = can("vendor:manage");
  const baseCurrency = me?.organization.base_currency ?? "JPY";
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const contracts = useQuery({
    queryKey: ["contracts", status],
    queryFn: () => api.get<Contract[]>("/contracts", status ? { status } : undefined),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["contracts"] });

  return (
    <>
      <PageHeader title={t("contracts.title")} subtitle={t("contracts.subtitle")} />
      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <Card
          title={t("contracts.title")}
          actions={
            <Select aria-label={t("contracts.status")} value={status} onChange={(e) => setStatus(e.target.value)} className="w-44">
              <option value="">{t("contracts.allStatuses")}</option>
              {(["draft", "active", "expired", "terminated"] as const).map((s) => (
                <option key={s} value={s}>
                  {t(`contracts.states.${s}`)}
                </option>
              ))}
            </Select>
          }
        >
          <ErrorNotice error={contracts.error} />
          {contracts.isLoading ? (
            <Spinner />
          ) : !contracts.data?.length ? (
            <EmptyState title={t("contracts.empty")}>{t("contracts.emptyBody")}</EmptyState>
          ) : (
            <div className="space-y-4">
              {contracts.data.map((c) => (
                <ContractRow key={c.id} contract={c} canManage={canManage} onChanged={refresh} />
              ))}
            </div>
          )}
          <p className="mt-4 text-xs text-muted">{t("contracts.remindNote")}</p>
        </Card>
        {canManage ? (
          <NewContract baseCurrency={baseCurrency} onCreated={refresh} />
        ) : (
          <Card title={t("contracts.newTitle")}>
            <p className="text-sm text-muted">{t("contracts.readOnly")}</p>
          </Card>
        )}
      </div>
    </>
  );
}

function ContractRow({ contract: c, canManage, onChanged }: { contract: Contract; canManage: boolean; onChanged: () => void }) {
  const { t } = useTranslation();
  const labels = useLabels();
  const [terminating, setTerminating] = useState(false);
  const [reason, setReason] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const act = useMutation({
    mutationFn: (action: "activate" | "terminate") =>
      api.post<Contract>(`/contracts/${c.id}/${action}`, action === "terminate" ? { reason } : undefined),
    onSuccess: () => {
      setTerminating(false);
      onChanged();
    },
  });
  const attach = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error(t("p2p.files.chooseFile"));
      return api.post<Contract>(`/contracts/${c.id}/document`, { filename: file.name, content_base64: await toBase64(file) });
    },
    onSuccess: () => {
      setFile(null);
      onChanged();
    },
  });
  const download = useMutation({ mutationFn: () => downloadStored(`/contracts/${c.id}/document`, c.document?.filename ?? "contract") });
  const canAttach = canManage && (c.status === "draft" || (c.status === "active" && !c.document));

  return (
    <article className="rounded-lg border border-border p-4" aria-label={c.number}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold">
            {c.number} · {c.title}
          </p>
          <p className="text-sm text-muted">
            {c.vendor_name} · {date(c.starts_on)} – {date(c.ends_on)}
          </p>
          <p className="mt-1 text-xs text-muted">
            {c.categories.length ? c.categories.map((k) => labels.category(k)).join(" · ") : t("contracts.categoriesHint")}
          </p>
        </div>
        <Badge tone={STATE_TONE[c.status]}>{t(`contracts.states.${c.status}`)}</Badge>
      </div>
      <Table>
        <thead>
          <tr>
            <Th>{t("contracts.cap")}</Th>
            <Th>{t("contracts.committed")}</Th>
            <Th>{t("contracts.file")}</Th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <Td className="tabular">{c.value_cap ? money(c.value_cap, c.currency) : t("contracts.noCap")}</Td>
            <Td className="tabular">
              <span title={t("contracts.committedHint", { currency: c.base_currency })}>{money(c.committed, c.base_currency)}</span>
            </Td>
            <Td>
              {c.document ? (
                <Button size="sm" variant="ghost" loading={download.isPending} onClick={() => download.mutate()}>
                  <Download className="size-4" /> {c.document.filename}
                  <span className="text-xs text-muted">({t(`contracts.scan.${c.document.scan_status}`)})</span>
                </Button>
              ) : (
                "—"
              )}
            </Td>
          </tr>
        </tbody>
      </Table>
      {c.terminated_reason && <p className="mt-2 text-sm text-muted">{c.terminated_reason}</p>}
      {canManage && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          {c.allowed_actions.includes("activate") && (
            <Button size="sm" loading={act.isPending && act.variables === "activate"} onClick={() => act.mutate("activate")}>
              {t("contracts.activate")}
            </Button>
          )}
          {c.allowed_actions.includes("terminate") && !terminating && (
            <Button size="sm" variant="secondary" onClick={() => setTerminating(true)}>
              {t("contracts.terminate")}
            </Button>
          )}
          {canAttach && (
            <>
              <label className="text-sm">
                {t("contracts.file")}
                <input type="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="mt-1 block text-sm" />
              </label>
              <Button size="sm" variant="secondary" loading={attach.isPending} disabled={!file} onClick={() => attach.mutate()}>
                <Paperclip className="size-4" /> {t("contracts.attach")}
              </Button>
            </>
          )}
        </div>
      )}
      {terminating && (
        <div className="mt-3 space-y-2">
          <Field label={t("contracts.terminateReason")}>
            <Textarea value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <div className="flex gap-2">
            <Button size="sm" variant="danger" disabled={reason.trim().length < 3} loading={act.isPending} onClick={() => act.mutate("terminate")}>
              {t("contracts.confirmTerminate")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setTerminating(false)}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}
      <div className="mt-2">
        <ErrorNotice error={act.error ?? attach.error ?? download.error} />
      </div>
    </article>
  );
}

function NewContract({ baseCurrency, onCreated }: { baseCurrency: string; onCreated: () => void }) {
  const { t } = useTranslation();
  const labels = useLabels();
  const vendors = useQuery({ queryKey: ["vendors", "all"], queryFn: () => api.get<Page<Vendor>>("/vendors", { limit: 200 }) });
  const rates = useQuery({ queryKey: ["fx-rates"], queryFn: () => api.get<RatesResponse>("/fx/rates") });
  const empty: Draft = { vendor_id: "", title: "", categories: [], starts_on: "", ends_on: "", value_cap: "", currency: baseCurrency };
  const [form, setForm] = useState<Draft>(empty);
  const create = useMutation({
    mutationFn: () => api.post<Contract>("/contracts", { ...form, value_cap: form.value_cap || null }),
    onSuccess: () => {
      setForm(empty);
      onCreated();
    },
  });
  const set = (k: keyof Draft) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const toggle = (k: string) =>
    setForm((f) => ({ ...f, categories: f.categories.includes(k) ? f.categories.filter((c) => c !== k) : [...f.categories, k] }));
  const currencies = [baseCurrency, ...(rates.data?.supported_currencies ?? []).filter((c) => c !== baseCurrency)];
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <Card title={t("contracts.newTitle")}>
      <form onSubmit={submit} className="space-y-3">
        <Field label={t("contracts.vendor")}>
          <Select required value={form.vendor_id} onChange={set("vendor_id")}>
            <option value="">{t("common.selectPlaceholder")}</option>
            {vendors.data?.items.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("contracts.contractTitle")}>
          <Input required minLength={3} value={form.title} onChange={set("title")} />
        </Field>
        <fieldset>
          <legend className="text-sm font-medium">{t("contracts.categories")}</legend>
          <p className="text-xs text-muted">{t("contracts.categoriesHint")}</p>
          <div className="mt-1 grid grid-cols-2 gap-1">
            {CATEGORY_KEYS.map((k) => (
              <label key={k} className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={form.categories.includes(k)} onChange={() => toggle(k)} />
                {labels.category(k)}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("contracts.startsOn")}>
            <Input type="date" required value={form.starts_on} onChange={set("starts_on")} />
          </Field>
          <Field label={t("contracts.endsOn")}>
            <Input type="date" required min={form.starts_on || undefined} value={form.ends_on} onChange={set("ends_on")} />
          </Field>
        </div>
        <div className="grid grid-cols-[1fr_100px] gap-3">
          <Field label={t("contracts.valueCap")} hint={t("contracts.valueCapHint")}>
            <Input type="number" min="0" step="any" value={form.value_cap} onChange={set("value_cap")} />
          </Field>
          <Field label={t("contracts.currency")}>
            <Select value={form.currency} onChange={set("currency")}>
              {currencies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <ErrorNotice error={create.error} />
        <Button type="submit" loading={create.isPending}>
          {t("contracts.create")}
        </Button>
      </form>
    </Card>
  );
}
