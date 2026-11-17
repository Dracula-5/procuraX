import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { saveBase64, saveBlob } from "../lib/files";
import { dateTime } from "../lib/format";
import { CATEGORY_KEYS, useLabels } from "../lib/labels";
import type { AccountMapping, CostCenter, JournalExport, LedgerSettings } from "../lib/types";

type Ledger = { settings: LedgerSettings | null; mappings: AccountMapping[] };
type Pending = { configured: boolean; invoices: number; payments: number };
type ExportFile = JournalExport & { content_base64: string; more_pending: boolean };

const EMPTY: LedgerSettings = { payable_account: "", input_tax_account: "", bank_account: "", default_expense_account: "" };
const ACCOUNT_FIELDS = [
  ["payable_account", "ledger.payable"],
  ["input_tax_account", "ledger.inputTax"],
  ["bank_account", "ledger.bank"],
  ["default_expense_account", "ledger.defaultExpense"],
] as const;

/** Ledger accounts, expense-account mapping and the once-only journal export for the ERP. */
export function Ledger() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const canConfigure = can("organization:manage");
  const canExport = can("payment:approve");
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: () => api.get<Ledger>("/ledger/settings") });

  if (ledger.isLoading) return <Spinner />;
  return (
    <>
      <PageHeader title={t("ledger.title")} subtitle={t("ledger.subtitle")} />
      <ErrorNotice error={ledger.error} />
      <div className="grid gap-6 xl:grid-cols-2">
        <Accounts key={JSON.stringify(ledger.data?.settings)} current={ledger.data?.settings ?? null} canEdit={canConfigure} />
        <Mappings key={JSON.stringify(ledger.data?.mappings)} current={ledger.data?.mappings ?? []} canEdit={canConfigure} />
        {canExport && <ExportPanel />}
      </div>
    </>
  );
}

function Accounts({ current, canEdit }: { current: LedgerSettings | null; canEdit: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [form, setForm] = useState<LedgerSettings>(current ?? EMPTY);
  const save = useMutation({
    mutationFn: () => api.put<Ledger>("/ledger/settings", form),
    onSuccess: (data) => {
      qc.setQueryData(["ledger"], data);
      void qc.invalidateQueries({ queryKey: ["journal-pending"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <Card title={t("ledger.accountsTitle")}>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
        {ACCOUNT_FIELDS.map(([key, label]) => (
          <Field key={key} label={t(label)}>
            <Input name={key} required maxLength={40} disabled={!canEdit} value={form[key]} onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
          </Field>
        ))}
        <div className="sm:col-span-2">
          <ErrorNotice error={save.error} />
          {save.isSuccess && <p className="text-sm text-ok">{t("ledger.saved")}</p>}
          {canEdit ? (
            <Button type="submit" loading={save.isPending}>
              {t("ledger.saveAccounts")}
            </Button>
          ) : (
            <p className="text-sm text-muted">{t("ledger.readOnly")}</p>
          )}
        </div>
      </form>
    </Card>
  );
}

function Mappings({ current, canEdit }: { current: AccountMapping[]; canEdit: boolean }) {
  const { t } = useTranslation();
  const labels = useLabels();
  const qc = useQueryClient();
  const centres = useQuery({ queryKey: ["cost-centers"], queryFn: () => api.get<CostCenter[]>("/cost-centers") });
  const [rows, setRows] = useState<AccountMapping[]>(current);
  const save = useMutation({
    mutationFn: () =>
      api.put<Ledger>(
        "/ledger/mappings",
        rows.map((r) => ({ category: r.category || null, cost_center_id: r.cost_center_id || null, expense_account: r.expense_account })),
      ),
    onSuccess: (data) => qc.setQueryData(["ledger"], data),
  });
  const update = (i: number, patch: Partial<AccountMapping>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <Card
      title={t("ledger.mappingsTitle")}
      actions={
        canEdit ? (
          <Button size="sm" variant="secondary" onClick={() => setRows((rs) => [...rs, { category: null, cost_center_id: null, expense_account: "" }])}>
            <Plus className="size-4" /> {t("ledger.addMapping")}
          </Button>
        ) : undefined
      }
    >
      <p className="mb-3 text-sm text-muted">{t("ledger.mappingsIntro")}</p>
      {!rows.length ? (
        <p className="text-sm text-muted">{t("ledger.noMappings")}</p>
      ) : (
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="grid grid-cols-2 gap-2 sm:grid-cols-[1fr_1fr_110px_36px]">
              <Select aria-label={t("common.category")} disabled={!canEdit} value={r.category ?? ""} onChange={(e) => update(i, { category: e.target.value || null })}>
                <option value="">{t("ledger.anyCategory")}</option>
                {CATEGORY_KEYS.map((k) => (
                  <option key={k} value={k}>
                    {labels.category(k)}
                  </option>
                ))}
              </Select>
              <Select aria-label={t("requestForm.costCentre")} disabled={!canEdit} value={r.cost_center_id ?? ""} onChange={(e) => update(i, { cost_center_id: e.target.value || null })}>
                <option value="">{t("ledger.anyCostCentre")}</option>
                {centres.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.code} · {c.name}
                  </option>
                ))}
              </Select>
              <Input aria-label={t("ledger.account")} placeholder={t("ledger.account")} disabled={!canEdit} maxLength={40} value={r.expense_account} onChange={(e) => update(i, { expense_account: e.target.value })} />
              {canEdit && (
                <Button type="button" variant="ghost" size="sm" className="h-10" aria-label={t("ledger.removeMapping")} onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}>
                  <Trash2 className="size-4" />
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="mt-3">
        <ErrorNotice error={save.error} />
        {save.isSuccess && <p className="text-sm text-ok">{t("ledger.saved")}</p>}
        {canEdit && (
          <Button size="sm" className="mt-2" loading={save.isPending} onClick={() => save.mutate()}>
            {t("ledger.saveMappings")}
          </Button>
        )}
      </div>
    </Card>
  );
}

function ExportPanel() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const pending = useQuery({ queryKey: ["journal-pending"], queryFn: () => api.get<Pending>("/journal-exports/pending") });
  const history = useQuery({ queryKey: ["journal-exports"], queryFn: () => api.get<JournalExport[]>("/journal-exports") });
  const run = useMutation({
    mutationFn: () => api.post<ExportFile>("/journal-exports", { format }),
    onSuccess: (file) => {
      saveBase64(file.content_base64, file.filename, file.format === "csv" ? "text/csv" : "application/json");
      void qc.invalidateQueries({ queryKey: ["journal-pending"] });
      void qc.invalidateQueries({ queryKey: ["journal-exports"] });
    },
  });
  const download = useMutation({
    mutationFn: async (e: JournalExport) => saveBlob(await api.blob(`/journal-exports/${e.id}/download`), e.filename),
  });
  const p = pending.data;

  return (
    <Card title={t("ledger.exportTitle")} className="xl:col-span-2">
      <p className="mb-3 text-sm text-muted">{t("ledger.entryRules")}</p>
      {p && <p className="mb-3 text-sm">{p.configured ? t("ledger.pending", { invoices: p.invoices, payments: p.payments }) : t("ledger.notConfigured")}</p>}
      <div className="flex flex-wrap items-end gap-3">
        <Field label={t("ledger.format")}>
          <Select value={format} onChange={(e) => setFormat(e.target.value as "csv" | "json")} className="w-32">
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </Select>
        </Field>
        <Button loading={run.isPending} disabled={!p?.configured || !(p.invoices + p.payments)} onClick={() => run.mutate()}>
          <Download className="size-4" /> {t("ledger.export")}
        </Button>
      </div>
      <div className="mt-3">
        <ErrorNotice error={run.error ?? pending.error ?? download.error} />
        {run.isSuccess && (
          <p className="text-sm text-ok">
            {t("ledger.exported", { reference: run.data.reference, entries: run.data.entry_count })}
            {run.data.more_pending && <span className="block text-muted">{t("ledger.morePending")}</span>}
          </p>
        )}
      </div>
      <h3 className="mt-6 mb-2 text-sm font-semibold">{t("ledger.historyTitle")}</h3>
      {history.isLoading ? (
        <Spinner />
      ) : !history.data?.length ? (
        <EmptyState title={t("ledger.empty")} />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t("ledger.reference")}</Th>
              <Th>{t("ledger.created")}</Th>
              <Th>{t("ledger.entries")}</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {history.data.map((e) => (
              <tr key={e.id}>
                <Td className="font-mono text-xs">{e.reference}</Td>
                <Td>{dateTime(e.created_at)}</Td>
                <Td>
                  {e.entry_count}
                  <span className="block text-xs text-muted">{t("ledger.documents", { invoices: e.invoice_count, payments: e.payment_count })}</span>
                </Td>
                <Td className="text-right">
                  <Button size="sm" variant="ghost" onClick={() => download.mutate(e)} title={e.sha256}>
                    <Download className="size-4" /> {t("ledger.download")}
                  </Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
