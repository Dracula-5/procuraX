import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { currentFiscalYear, money, percent } from "../lib/format";
import type { Budget, CostCenter } from "../lib/types";

export function Budgets() {
  const { t } = useTranslation();
  const { can, me } = useAuth();
  const qc = useQueryClient();
  const budgets = useQuery({ queryKey: ["budgets"], queryFn: () => api.get<Budget[]>("/budgets") });
  const costCenters = useQuery({ queryKey: ["cost-centers"], queryFn: () => api.get<CostCenter[]>("/cost-centers"), enabled: can("budget:manage") });
  const [form, setForm] = useState(() => ({
    cost_center_id: "",
    fiscal_year: currentFiscalYear(me?.organization.fiscal_year_start_month ?? 4),
    amount: "",
  }));
  const create = useMutation({
    mutationFn: () => api.post<Budget>("/budgets", form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["budgets"] });
      setForm((f) => ({ ...f, amount: "" }));
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <>
      <PageHeader title={t("budgets.title")} subtitle={t("budgets.subtitle")} />
      {can("budget:manage") && (
        <Card title={t("budgets.setTitle")} className="mb-6">
          <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_140px_200px_auto] sm:items-end">
            <Field label={t("requestForm.costCentre")}>
              <Select required value={form.cost_center_id} onChange={(e) => setForm((f) => ({ ...f, cost_center_id: e.target.value }))}>
                <option value="">{t("common.selectPlaceholder")}</option>
                {costCenters.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.code} · {c.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t("requestDetail.fiscalYear")}>
              <Input type="number" min={2000} max={2100} value={form.fiscal_year} onChange={(e) => setForm((f) => ({ ...f, fiscal_year: Number(e.target.value) }))} />
            </Field>
            <Field label={t("budgets.amountIn", { currency: me?.organization.base_currency })}>
              <Input required type="number" min={0} value={form.amount} onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value }))} />
            </Field>
            <Button type="submit" loading={create.isPending}>
              {t("common.save")}
            </Button>
          </form>
          <div className="mt-3">
            <ErrorNotice error={create.error} />
          </div>
        </Card>
      )}
      <ErrorNotice error={budgets.error} />
      {budgets.isLoading ? (
        <Spinner />
      ) : !budgets.data?.length ? (
        <EmptyState title={t("budgets.emptyTitle")}>{t("budgets.emptyBody")}</EmptyState>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t("requestForm.costCentre")}</Th>
              <Th>{t("budgets.fy")}</Th>
              <Th className="text-right">{t("policyPanel.budget")}</Th>
              <Th className="text-right">{t("budgets.committed")}</Th>
              <Th className="text-right">{t("budgets.pending")}</Th>
              <Th className="text-right">{t("budgets.available")}</Th>
              <Th>{t("budgets.utilisation")}</Th>
            </tr>
          </thead>
          <tbody>
            {budgets.data.map((b) => {
              const u = Number(b.utilization ?? 0);
              return (
                <tr key={b.id}>
                  <Td>
                    <p className="font-medium">{b.cost_center_code}</p>
                    <p className="text-xs text-muted">{b.cost_center_name}</p>
                  </Td>
                  <Td>{t("common.fy", { year: b.fiscal_year })}</Td>
                  <Td className="tabular text-right">{money(b.amount, b.currency)}</Td>
                  <Td className="tabular text-right">{money(b.committed, b.currency)}</Td>
                  <Td className="tabular text-right text-muted">{money(b.pending, b.currency)}</Td>
                  <Td className={clsx("tabular text-right", Number(b.available) < 0 && "text-danger")}>{money(b.available, b.currency)}</Td>
                  <Td className="w-40">
                    <div className="h-2 overflow-hidden rounded-full bg-surface-2">
                      <div className={clsx("h-full", u > 1 ? "bg-danger" : u >= 0.9 ? "bg-warn" : "bg-primary")} style={{ width: `${Math.min(u, 1) * 100}%` }} />
                    </div>
                    <p className="tabular mt-1 text-xs text-muted">{percent(b.utilization)}</p>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </>
  );
}
