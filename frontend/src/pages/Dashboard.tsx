import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { PRStatusBadge } from "../components/badges";
import { Card, EmptyState, ErrorNotice, PageHeader, Spinner, Stat } from "../components/ui";
import { api } from "../lib/api";
import { money, monthLabel, number, percent, relative } from "../lib/format";
import { CATEGORY_KEYS, useLabels } from "../lib/labels";
import type { Budget, InboxItem, Page, PRStats, PRSummary } from "../lib/types";

type SpendAnalytics = {
  approved_spend_by_category: { category: string; approved_requests: number; approved_value: string }[];
  approved_spend_by_department: { department_id: string | null; department_name: string; approved_requests: number; approved_value: string }[];
  approved_spend_by_vendor: { vendor_id: string | null; vendor_name: string; approved_requests: number; approved_value: string }[];
  invoice_match_summary: { status: string; count: number; total: string }[];
  purchase_order_summary: { status: string; count: number; total: string }[];
  payment_summary: { status: string; count: number; total: string }[];
  request_decision_cycle_hours: { completed_requests: number; mean: number | null; median: number | null; p90: number | null; max: number | null };
  approved_spend_monthly_last_12_months: { month: string; approved_requests: number; approved_value: string }[];
};

type SpendDimensionRow = { key: string; name: string; approved_requests: number; approved_value: string };

function downloadSpendCsv(data: SpendAnalytics, currency: string) {
  // Column names and codes stay in English: the file is for systems and spreadsheets, not the screen.
  const rows: string[][] = [["measure", "dimension", "name", "requests", "value", "currency"]];
  data.approved_spend_by_category.forEach((row) => rows.push(["approved_spend", "category", row.category, String(row.approved_requests), row.approved_value, currency]));
  data.approved_spend_by_department.forEach((row) => rows.push(["approved_spend", "department", row.department_name, String(row.approved_requests), row.approved_value, currency]));
  data.approved_spend_by_vendor.forEach((row) => rows.push(["approved_spend", "vendor", row.vendor_name, String(row.approved_requests), row.approved_value, currency]));
  data.approved_spend_monthly_last_12_months.forEach((row) => rows.push(["approved_spend", "month", row.month, String(row.approved_requests), row.approved_value, currency]));
  const csv = rows.map((row) => row.map((value) => `"${value.replaceAll('"', '""')}"`).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `procurax-spend-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function Bars({ rows, currency }: { rows: SpendDimensionRow[]; currency: string }) {
  const { t } = useTranslation();
  const max = Math.max(...rows.map((row) => Number(row.approved_value)), 1);
  return (
    <ul className="space-y-3">
      {rows.map((row) => (
        <li key={row.key}>
          <div className="mb-1 flex justify-between gap-3 text-sm">
            <span>{row.name}</span>
            <span className="tabular">
              {money(row.approved_value, currency)} · {t("dashboard.requestCount", { count: row.approved_requests })}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded bg-surface-2">
            <div className="h-full rounded bg-primary" style={{ width: `${Math.max(3, (Number(row.approved_value) / max) * 100)}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function SpendDimension({ title, rows, currency, emptyTitle, emptyBody }: { title: string; rows: SpendDimensionRow[]; currency: string; emptyTitle: string; emptyBody: string }) {
  return <Card title={title}>{rows.length ? <Bars rows={rows} currency={currency} /> : <EmptyState title={emptyTitle}>{emptyBody}</EmptyState>}</Card>;
}

const hours = (value: number | null) => (value == null ? "—" : `${number(value, 1)} h`);
const filterClass = "mt-1 block h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-foreground";

export function Dashboard() {
  const { t } = useTranslation();
  const labels = useLabels();
  const { me, can } = useAuth();
  const currency = me?.organization.base_currency ?? "JPY";
  const emptyFilters = { from_date: "", to_date: "", department_id: "", vendor_id: "", category: "" };
  const [analyticsFilters, setAnalyticsFilters] = useState(emptyFilters);
  const setFilter = (key: keyof typeof emptyFilters) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setAnalyticsFilters((current) => ({ ...current, [key]: event.target.value }));
  const stats = useQuery({ queryKey: ["pr-stats"], queryFn: () => api.get<PRStats>("/purchase-requests/stats") });
  const mine = useQuery({
    queryKey: ["requests", { mine: true, limit: 5 }],
    queryFn: () => api.get<Page<PRSummary>>("/purchase-requests", { mine: true, limit: 5 }),
  });
  const inbox = useQuery({
    queryKey: ["inbox"],
    queryFn: () => api.get<InboxItem[]>("/approvals/inbox"),
    enabled: can("approval:act"),
  });
  const budgets = useQuery({
    queryKey: ["budgets", "current-fy"],
    queryFn: () => api.get<Budget[]>("/budgets", { fiscal_year: stats.data?.fiscal_year }),
    enabled: can("budget:read") && stats.data !== undefined,
  });
  const analytics = useQuery({
    queryKey: ["spend-analytics", analyticsFilters],
    queryFn: () => api.get<SpendAnalytics>("/spend-analytics", analyticsFilters),
    enabled: can("analytics:read"),
  });
  const analyticsOptions = useQuery({
    queryKey: ["spend-analytics-options"],
    queryFn: () => api.get<SpendAnalytics>("/spend-analytics"),
    enabled: can("analytics:read"),
  });

  if (stats.isLoading) return <Spinner />;
  const s = stats.data;
  const a = analytics.data;

  return (
    <>
      <PageHeader
        title={t("dashboard.welcome", { name: me?.full_name.split(" ")[0] })}
        subtitle={t("dashboard.subtitle")}
        actions={
          can("purchase_request:create") && (
            <Link to="/app/requests/new" className="inline-flex h-10 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-fg hover:bg-primary-hover">
              <Plus className="size-4" /> {t("requests.new")}
            </Link>
          )
        }
      />
      <ErrorNotice error={stats.error} />
      {s && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label={t("dashboard.stat.myOpen")} value={s.my_open} hint={t("dashboard.stat.myOpenHint")} />
          <Stat
            label={t("dashboard.stat.awaitingMe")}
            value={s.awaiting_my_approval}
            hint={s.overdue_approvals_for_me ? t("dashboard.stat.pastSla", { count: s.overdue_approvals_for_me }) : t("dashboard.stat.nonePastSla")}
          />
          <Stat label={t("dashboard.stat.pending")} value={s.by_status.pending_approval} hint={t("dashboard.stat.visibleToYou")} />
          <Stat label={t("dashboard.stat.approvedFy", { year: s.fiscal_year })} value={money(s.approved_value_current_fy, s.currency)} hint={t("dashboard.stat.committedHint")} />
        </div>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title={t("dashboard.myRecent")} actions={<Link to="/app/requests?mine=1" className="text-sm text-primary hover:underline">{t("common.viewAll")}</Link>}>
          {mine.data?.items.length ? (
            <ul className="divide-y divide-border">
              {mine.data.items.map((pr) => (
                <li key={pr.id}>
                  <Link to={`/app/requests/${pr.id}`} className="flex items-center justify-between gap-3 py-2.5 hover:text-primary">
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{pr.title}</span>
                      <span className="text-xs text-muted">
                        {pr.number} · {relative(pr.updated_at)}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-3">
                      <span className="tabular text-sm">{money(pr.estimated_total, pr.currency)}</span>
                      <PRStatusBadge status={pr.status} />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title={t("dashboard.noRequestsTitle")}>{t("dashboard.noRequestsBody")}</EmptyState>
          )}
        </Card>

        {can("approval:act") ? (
          <Card title={t("dashboard.needsDecision")} actions={<Link to="/app/approvals" className="text-sm text-primary hover:underline">{t("dashboard.openInbox")}</Link>}>
            {inbox.data?.length ? (
              <ul className="divide-y divide-border">
                {inbox.data.slice(0, 5).map((i) => (
                  <li key={i.approval_id}>
                    <Link to={`/app/requests/${i.purchase_request_id}`} className="flex items-center justify-between gap-3 py-2.5 hover:text-primary">
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium">{i.title}</span>
                        <span className="text-xs text-muted">
                          {i.number} · {i.requester_name}
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-2 text-sm">
                        {i.is_overdue && <AlertTriangle className="size-4 text-warn" aria-label={t("common.pastSla")} />}
                        <span className="tabular">{money(i.estimated_total, i.currency)}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title={t("dashboard.inboxClearTitle")}>{t("dashboard.inboxClearBody")}</EmptyState>
            )}
          </Card>
        ) : (
          s && (
            <Card title={t("dashboard.byStatus")}>
              <ul className="space-y-2 text-sm">
                {Object.entries(s.by_status).map(([k, v]) => (
                  <li key={k} className="flex justify-between">
                    <span className="text-muted">{labels.prStatus(k)}</span>
                    <span className="tabular font-medium">{v}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )
        )}
      </div>
      {can("budget:read") && (
        <section className="mt-6" aria-label={t("dashboard.budgetSection")}>
          <Card title={t("dashboard.budgetTitle", { year: s?.fiscal_year ?? "" })} actions={<Link to="/app/budgets" className="text-sm text-primary hover:underline">{t("dashboard.viewBudgets")}</Link>}>
            {budgets.isLoading ? <Spinner label={t("dashboard.loadingBudgets")} /> : null}
            <ErrorNotice error={budgets.error} />
            {budgets.data?.length ? (
              <ul className="space-y-4">
                {budgets.data.slice(0, 5).map((budget) => {
                  const utilization = Number(budget.utilization ?? 0);
                  return (
                    <li key={budget.id}>
                      <div className="mb-1 flex flex-wrap justify-between gap-x-4 gap-y-1 text-sm">
                        <span className="font-medium">
                          {budget.cost_center_code} · {budget.cost_center_name}
                        </span>
                        <span className="tabular text-muted">
                          {t("dashboard.committedOfBudget", { committed: money(budget.committed, budget.currency), budget: money(budget.amount, budget.currency) })}
                        </span>
                      </div>
                      <div className="h-2 overflow-hidden rounded bg-surface-2">
                        <div className={`h-full rounded ${utilization > 1 ? "bg-danger" : utilization >= 0.9 ? "bg-warn" : "bg-primary"}`} style={{ width: `${Math.min(100, Math.max(0, utilization * 100))}%` }} />
                      </div>
                      <p className="mt-1 flex justify-between text-xs text-muted">
                        <span>{t("dashboard.utilizedPending", { percent: percent(utilization, 0), pending: money(budget.pending, budget.currency) })}</span>
                        <span>{t("dashboard.available", { amount: money(budget.available, budget.currency) })}</span>
                      </p>
                    </li>
                  );
                })}
              </ul>
            ) : !budgets.isLoading && !budgets.error ? (
              <EmptyState title={t("dashboard.noBudgetsTitle")}>{t("dashboard.noBudgetsBody")}</EmptyState>
            ) : null}
            {budgets.data && budgets.data.length > 5 && <p className="mt-3 text-xs text-muted">{t("dashboard.showingBudgets", { total: budgets.data.length })}</p>}
          </Card>
        </section>
      )}
      {can("analytics:read") && (
        <section className="mt-6" aria-label={t("dashboard.analyticsSection")}>
          <PageHeader title={t("dashboard.analyticsTitle")} subtitle={t("dashboard.analyticsSubtitle")} />
          {analytics.isLoading ? <Spinner label={t("dashboard.loadingAnalytics")} /> : null}
          <ErrorNotice error={analytics.error} />
          <div className="my-4 grid gap-3 rounded-lg border border-border bg-surface p-4 sm:grid-cols-2 xl:grid-cols-6">
            <label className="text-xs font-medium text-muted">
              {t("dashboard.filter.from")}
              <input aria-label={t("dashboard.filter.fromAria")} type="date" value={analyticsFilters.from_date} max={analyticsFilters.to_date || undefined} onChange={setFilter("from_date")} className={filterClass} />
            </label>
            <label className="text-xs font-medium text-muted">
              {t("dashboard.filter.to")}
              <input aria-label={t("dashboard.filter.toAria")} type="date" value={analyticsFilters.to_date} min={analyticsFilters.from_date || undefined} onChange={setFilter("to_date")} className={filterClass} />
            </label>
            <label className="text-xs font-medium text-muted">
              {t("dashboard.filter.department")}
              <select aria-label={t("dashboard.filter.departmentAria")} value={analyticsFilters.department_id} onChange={setFilter("department_id")} className={filterClass}>
                <option value="">{t("dashboard.filter.allDepartments")}</option>
                {analyticsOptions.data?.approved_spend_by_department.filter((row) => row.department_id).map((row) => <option key={row.department_id} value={row.department_id!}>{row.department_name}</option>)}
              </select>
            </label>
            <label className="text-xs font-medium text-muted">
              {t("dashboard.filter.vendor")}
              <select aria-label={t("dashboard.filter.vendorAria")} value={analyticsFilters.vendor_id} onChange={setFilter("vendor_id")} className={filterClass}>
                <option value="">{t("dashboard.filter.allVendors")}</option>
                {analyticsOptions.data?.approved_spend_by_vendor.filter((row) => row.vendor_id).map((row) => <option key={row.vendor_id} value={row.vendor_id!}>{row.vendor_name}</option>)}
              </select>
            </label>
            <label className="text-xs font-medium text-muted">
              {t("common.category")}
              <select aria-label={t("dashboard.filter.categoryAria")} value={analyticsFilters.category} onChange={setFilter("category")} className={filterClass}>
                <option value="">{t("requests.allCategories")}</option>
                {CATEGORY_KEYS.map((category) => <option key={category} value={category}>{labels.category(category)}</option>)}
              </select>
            </label>
            <div className="flex items-end gap-2">
              <button type="button" onClick={() => setAnalyticsFilters(emptyFilters)} className="h-9 rounded-md border border-border px-3 text-sm hover:bg-surface-2">{t("common.clear")}</button>
              <button type="button" disabled={!a} onClick={() => a && downloadSpendCsv(a, currency)} className="h-9 rounded-md bg-primary px-3 text-sm font-medium text-primary-fg disabled:opacity-50">{t("common.exportCsv")}</button>
            </div>
          </div>
          {a && (
            <div className="grid gap-4 lg:grid-cols-2">
              <SpendDimension
                title={t("dashboard.byCategory")}
                currency={currency}
                emptyTitle={t("dashboard.noSpendTitle")}
                emptyBody={t("dashboard.noSpendCategory")}
                rows={a.approved_spend_by_category.map((row) => ({ key: row.category, name: labels.category(row.category), approved_requests: row.approved_requests, approved_value: row.approved_value }))}
              />
              <Card title={t("dashboard.processHealth")}>
                <div className="grid grid-cols-2 gap-3">
                  <Stat label={t("dashboard.cycle.decided")} value={a.request_decision_cycle_hours.completed_requests} hint={t("dashboard.cycle.decidedHint")} />
                  <Stat label={t("dashboard.cycle.mean")} value={hours(a.request_decision_cycle_hours.mean)} hint={t("dashboard.cycle.meanHint")} />
                  <Stat label={t("dashboard.cycle.median")} value={hours(a.request_decision_cycle_hours.median)} hint={t("dashboard.cycle.completedHint")} />
                  <Stat label={t("dashboard.cycle.p90")} value={hours(a.request_decision_cycle_hours.p90)} hint={t("dashboard.cycle.completedHint")} />
                  <Stat label={t("dashboard.cycle.max")} value={hours(a.request_decision_cycle_hours.max)} hint={t("dashboard.cycle.completedHint")} />
                  <Stat label={t("dashboard.cycle.invoicesReview")} value={a.invoice_match_summary.filter((i) => i.status !== "matched").reduce((n, i) => n + i.count, 0)} hint={t("dashboard.cycle.invoicesReviewHint")} />
                </div>
                <div className="mt-4 grid gap-4 border-t border-border pt-4 sm:grid-cols-3">
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase text-muted">{t("dashboard.orders")}</p>
                    {a.purchase_order_summary.map((x) => <p key={x.status} className="flex justify-between text-sm"><span>{labels.poStatus(x.status)}</span><span>{x.count}</span></p>)}
                  </div>
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase text-muted">{t("dashboard.invoiceMatching")}</p>
                    {a.invoice_match_summary.map((x) => <p key={x.status} className="flex justify-between text-sm"><span>{labels.matchStatus(x.status)}</span><span>{x.count}</span></p>)}
                  </div>
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase text-muted">{t("dashboard.paymentApprovals")}</p>
                    {a.payment_summary.map((x) => <p key={x.status} className="flex justify-between text-sm"><span>{labels.paymentStatus(x.status)}</span><span>{x.count}</span></p>)}
                  </div>
                </div>
              </Card>
              <SpendDimension
                title={t("dashboard.byDepartment")}
                currency={currency}
                emptyTitle={t("dashboard.noSpendTitle")}
                emptyBody={t("dashboard.noSpendBody")}
                rows={a.approved_spend_by_department.map((row) => ({ key: row.department_id ?? "unassigned", name: row.department_name, approved_requests: row.approved_requests, approved_value: row.approved_value }))}
              />
              <SpendDimension
                title={t("dashboard.byVendor")}
                currency={currency}
                emptyTitle={t("dashboard.noSpendTitle")}
                emptyBody={t("dashboard.noSpendBody")}
                rows={a.approved_spend_by_vendor.map((row) => ({ key: row.vendor_id ?? "unspecified", name: row.vendor_name, approved_requests: row.approved_requests, approved_value: row.approved_value }))}
              />
              <SpendDimension
                title={t("dashboard.trend")}
                currency={currency}
                emptyTitle={t("dashboard.noTrendTitle")}
                emptyBody={t("dashboard.noTrendBody")}
                rows={a.approved_spend_monthly_last_12_months.map((row) => ({ key: row.month, name: monthLabel(row.month), approved_requests: row.approved_requests, approved_value: row.approved_value }))}
              />
            </div>
          )}
        </section>
      )}
    </>
  );
}
