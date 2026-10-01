import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { AlertTriangle, Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { PRStatusBadge } from "../components/badges";
import { Card, EmptyState, ErrorNotice, PageHeader, Spinner, Stat } from "../components/ui";
import { api } from "../lib/api";
import { money, relative, STATUS_LABELS } from "../lib/format";
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

const SPEND_CATEGORIES = [
  "it_services", "travel", "facilities", "marketing", "professional_services",
  "hardware", "software", "logistics", "office_supplies", "manufacturing",
];

function downloadSpendCsv(data: SpendAnalytics, currency: string) {
  const rows: string[][] = [["measure", "dimension", "name", "requests", "value", "currency"]];
  data.approved_spend_by_category.forEach((row) => rows.push(["approved_spend", "category", row.category, String(row.approved_requests), row.approved_value, currency]));
  data.approved_spend_by_department.forEach((row) => rows.push(["approved_spend", "department", row.department_name, String(row.approved_requests), row.approved_value, currency]));
  data.approved_spend_by_vendor.forEach((row) => rows.push(["approved_spend", "vendor", row.vendor_name, String(row.approved_requests), row.approved_value, currency]));
  data.approved_spend_monthly_last_12_months.forEach((row) => rows.push(["approved_spend", "month", row.month, String(row.approved_requests), row.approved_value, currency]));
  const csv = rows.map((row) => row.map((value) => `"${value.replaceAll('"', '""')}"`).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `procurax-spend-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function SpendDimension({ title, rows, currency }: { title: string; rows: SpendDimensionRow[]; currency: string }) {
  const max = Math.max(...rows.map((row) => Number(row.approved_value)), 1);
  return (
    <Card title={title}>
      {rows.length ? (
        <ul className="space-y-3">
          {rows.map((row) => (
            <li key={row.key}>
              <div className="mb-1 flex justify-between gap-3 text-sm">
                <span>{row.name}</span>
                <span className="tabular">{money(row.approved_value, currency)} · {row.approved_requests} requests</span>
              </div>
              <div className="h-2 overflow-hidden rounded bg-surface-2">
                <div className="h-full rounded bg-primary" style={{ width: `${Math.max(3, Number(row.approved_value) / max * 100)}%` }} />
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState title="No approved spend yet">This breakdown appears when approved requests are recorded.</EmptyState>
      )}
    </Card>
  );
}

export function Dashboard() {
  const { me, can } = useAuth();
  const [analyticsFilters, setAnalyticsFilters] = useState({
    from_date: "",
    to_date: "",
    department_id: "",
    vendor_id: "",
    category: "",
  });
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

  return (
    <>
      <PageHeader
        title={`Welcome, ${me?.full_name.split(" ")[0]}`}
        subtitle="Live figures for the requests you can see. Nothing on this page is precomputed or sample data."
        actions={
          can("purchase_request:create") && (
            <Link to="/app/requests/new" className="inline-flex h-10 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-fg hover:bg-primary-hover">
              <Plus className="size-4" /> New request
            </Link>
          )
        }
      />
      <ErrorNotice error={stats.error} />
      {s && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="My open requests" value={s.my_open} hint="Draft, pending or blocked" />
          <Stat
            label="Awaiting my approval"
            value={s.awaiting_my_approval}
            hint={s.overdue_approvals_for_me ? `${s.overdue_approvals_for_me} past SLA` : "None past SLA"}
          />
          <Stat label="Pending approval" value={s.by_status.pending_approval} hint="Visible to you" />
          <Stat label={`Approved value FY${s.fiscal_year}`} value={money(s.approved_value_current_fy, s.currency)} hint="Committed spend visible to you" />
        </div>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card
          title="My recent requests"
          actions={<Link to="/app/requests?mine=1" className="text-sm text-primary hover:underline">View all</Link>}
        >
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
            <EmptyState title="No requests yet">Create a purchase request to start the workflow.</EmptyState>
          )}
        </Card>

        {can("approval:act") ? (
          <Card title="Needs your decision" actions={<Link to="/app/approvals" className="text-sm text-primary hover:underline">Open inbox</Link>}>
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
                        {i.is_overdue && <AlertTriangle className="size-4 text-warn" aria-label="Past SLA" />}
                        <span className="tabular">{money(i.estimated_total, i.currency)}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="Inbox clear">No approval steps are waiting on you.</EmptyState>
            )}
          </Card>
        ) : (
          s && (
            <Card title="Requests by status">
              <ul className="space-y-2 text-sm">
                {Object.entries(s.by_status).map(([k, v]) => (
                  <li key={k} className="flex justify-between">
                    <span className="text-muted">{STATUS_LABELS[k]}</span>
                    <span className="tabular font-medium">{v}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )
        )}
      </div>
      {can("budget:read") && (
        <section className="mt-6" aria-label="Current fiscal year budget utilization">
          <Card title={`Budget utilization FY${s?.fiscal_year ?? ""}`} actions={<Link to="/app/budgets" className="text-sm text-primary hover:underline">View budgets</Link>}>
            {budgets.isLoading ? <Spinner label="Loading budget utilization" /> : null}
            <ErrorNotice error={budgets.error} />
            {budgets.data?.length ? (
              <ul className="space-y-4">
                {budgets.data.slice(0, 5).map((budget) => {
                  const utilization = Number(budget.utilization ?? 0);
                  return (
                    <li key={budget.id}>
                      <div className="mb-1 flex flex-wrap justify-between gap-x-4 gap-y-1 text-sm">
                        <span className="font-medium">{budget.cost_center_code} · {budget.cost_center_name}</span>
                        <span className="tabular text-muted">{money(budget.committed, budget.currency)} committed / {money(budget.amount, budget.currency)} budget</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded bg-surface-2">
                        <div className={`h-full rounded ${utilization > 1 ? "bg-danger" : utilization >= 0.9 ? "bg-warn" : "bg-primary"}`} style={{ width: `${Math.min(100, Math.max(0, utilization * 100))}%` }} />
                      </div>
                      <p className="mt-1 flex justify-between text-xs text-muted"><span>{Math.round(utilization * 100)}% utilized · {money(budget.pending, budget.currency)} pending</span><span>{money(budget.available, budget.currency)} available</span></p>
                    </li>
                  );
                })}
              </ul>
            ) : !budgets.isLoading && !budgets.error ? (
              <EmptyState title="No current year budgets">Set budgets for cost centres to see live committed, pending, and available amounts.</EmptyState>
            ) : null}
            {budgets.data && budgets.data.length > 5 && <p className="mt-3 text-xs text-muted">Showing 5 of {budgets.data.length} cost centre budgets.</p>}
          </Card>
        </section>
      )}
      {can("analytics:read") && (
        <section className="mt-6" aria-label="Live procurement analytics">
          <PageHeader title="Spend and process signals" subtitle="Live workflow data from this tenant. These are operating measures, not claimed savings or ROI." />
          {analytics.isLoading ? <Spinner label="Loading spend analytics" /> : null}
          <ErrorNotice error={analytics.error} />
          <div className="my-4 grid gap-3 rounded-lg border border-border bg-surface p-4 sm:grid-cols-2 xl:grid-cols-6">
            <label className="text-xs font-medium text-muted">From date<input aria-label="Analytics from date" type="date" value={analyticsFilters.from_date} max={analyticsFilters.to_date || undefined} onChange={(event) => setAnalyticsFilters((current) => ({ ...current, from_date: event.target.value }))} className="mt-1 block h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-foreground" /></label>
            <label className="text-xs font-medium text-muted">To date<input aria-label="Analytics to date" type="date" value={analyticsFilters.to_date} min={analyticsFilters.from_date || undefined} onChange={(event) => setAnalyticsFilters((current) => ({ ...current, to_date: event.target.value }))} className="mt-1 block h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-foreground" /></label>
            <label className="text-xs font-medium text-muted">Department<select aria-label="Analytics department filter" value={analyticsFilters.department_id} onChange={(event) => setAnalyticsFilters((current) => ({ ...current, department_id: event.target.value }))} className="mt-1 block h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-foreground"><option value="">All departments</option>{analyticsOptions.data?.approved_spend_by_department.filter((row) => row.department_id).map((row) => <option key={row.department_id} value={row.department_id!}>{row.department_name}</option>)}</select></label>
            <label className="text-xs font-medium text-muted">Vendor<select aria-label="Analytics vendor filter" value={analyticsFilters.vendor_id} onChange={(event) => setAnalyticsFilters((current) => ({ ...current, vendor_id: event.target.value }))} className="mt-1 block h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-foreground"><option value="">All vendors</option>{analyticsOptions.data?.approved_spend_by_vendor.filter((row) => row.vendor_id).map((row) => <option key={row.vendor_id} value={row.vendor_id!}>{row.vendor_name}</option>)}</select></label>
            <label className="text-xs font-medium text-muted">Category<select aria-label="Analytics category filter" value={analyticsFilters.category} onChange={(event) => setAnalyticsFilters((current) => ({ ...current, category: event.target.value }))} className="mt-1 block h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-foreground"><option value="">All categories</option>{SPEND_CATEGORIES.map((category) => <option key={category} value={category}>{category.replaceAll("_", " ")}</option>)}</select></label>
            <div className="flex items-end gap-2"><button type="button" onClick={() => setAnalyticsFilters({ from_date: "", to_date: "", department_id: "", vendor_id: "", category: "" })} className="h-9 rounded-md border border-border px-3 text-sm hover:bg-surface-2">Clear</button><button type="button" disabled={!analytics.data} onClick={() => analytics.data && downloadSpendCsv(analytics.data, me?.organization.base_currency ?? "JPY")} className="h-9 rounded-md bg-primary px-3 text-sm font-medium text-primary-fg disabled:opacity-50">Export CSV</button></div>
          </div>
          {analytics.data && <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Approved request value by category">
              {analytics.data.approved_spend_by_category.length ? <ul className="space-y-3">{analytics.data.approved_spend_by_category.map(row => {
                const max = Math.max(...analytics.data!.approved_spend_by_category.map(item => Number(item.approved_value)), 1);
                const pct = Math.max(3, Number(row.approved_value) / max * 100);
                return <li key={row.category}><div className="mb-1 flex justify-between gap-3 text-sm"><span>{row.category.replaceAll("_", " ")}</span><span className="tabular">{money(row.approved_value, me?.organization.base_currency ?? "JPY")} · {row.approved_requests} requests</span></div><div className="h-2 overflow-hidden rounded bg-surface-2"><div className="h-full rounded bg-primary" style={{ width: `${pct}%` }} /></div></li>;
              })}</ul> : <EmptyState title="No approved spend yet">Category totals appear from approved requests.</EmptyState>}
            </Card>
            <Card title="Process health">
              <div className="grid grid-cols-2 gap-3"><Stat label="Decided requests" value={analytics.data.request_decision_cycle_hours.completed_requests} hint="Approved or rejected" /><Stat label="Mean decision cycle" value={analytics.data.request_decision_cycle_hours.mean == null ? "—" : `${analytics.data.request_decision_cycle_hours.mean} h`} hint="Submission to final decision" /><Stat label="Median decision cycle" value={analytics.data.request_decision_cycle_hours.median == null ? "—" : `${analytics.data.request_decision_cycle_hours.median} h`} hint="Completed requests" /><Stat label="90th percentile cycle" value={analytics.data.request_decision_cycle_hours.p90 == null ? "—" : `${analytics.data.request_decision_cycle_hours.p90} h`} hint="Completed requests" /><Stat label="Longest observed cycle" value={analytics.data.request_decision_cycle_hours.max == null ? "—" : `${analytics.data.request_decision_cycle_hours.max} h`} hint="Completed requests" /><Stat label="Invoices requiring review" value={analytics.data.invoice_match_summary.filter(i => i.status !== "matched").reduce((n, i) => n + i.count, 0)} hint="Mismatch, partial or review" /></div>
              <div className="mt-4 grid gap-4 border-t border-border pt-4 sm:grid-cols-3"><div><p className="mb-2 text-xs font-semibold uppercase text-muted">Orders</p>{analytics.data.purchase_order_summary.map(x => <p key={x.status} className="flex justify-between text-sm"><span>{x.status.replaceAll("_", " ")}</span><span>{x.count}</span></p>)}</div><div><p className="mb-2 text-xs font-semibold uppercase text-muted">Invoice matching</p>{analytics.data.invoice_match_summary.map(x => <p key={x.status} className="flex justify-between text-sm"><span>{x.status.replaceAll("_", " ")}</span><span>{x.count}</span></p>)}</div><div><p className="mb-2 text-xs font-semibold uppercase text-muted">Payment approvals</p>{analytics.data.payment_summary.map(x => <p key={x.status} className="flex justify-between text-sm"><span>{x.status.replaceAll("_", " ")}</span><span>{x.count}</span></p>)}</div></div>
            </Card>
            <SpendDimension
              title="Approved request value by department"
              currency={me?.organization.base_currency ?? "JPY"}
              rows={analytics.data.approved_spend_by_department.map((row) => ({
                key: row.department_id ?? "unassigned",
                name: row.department_name,
                approved_requests: row.approved_requests,
                approved_value: row.approved_value,
              }))}
            />
            <SpendDimension
              title="Approved request value by vendor"
              currency={me?.organization.base_currency ?? "JPY"}
              rows={analytics.data.approved_spend_by_vendor.map((row) => ({
                key: row.vendor_id ?? "unspecified",
                name: row.vendor_name,
                approved_requests: row.approved_requests,
                approved_value: row.approved_value,
              }))}
            />
            <Card title="Approved spend trend · last 12 months">
              {analytics.data.approved_spend_monthly_last_12_months.length ? (
                <ul className="space-y-3">
                  {analytics.data.approved_spend_monthly_last_12_months.map((row) => {
                    const max = Math.max(
                      ...analytics.data!.approved_spend_monthly_last_12_months.map((item) => Number(item.approved_value)),
                      1,
                    );
                    return (
                      <li key={row.month}>
                        <div className="mb-1 flex justify-between gap-3 text-sm">
                          <span>{new Date(row.month).toLocaleDateString(undefined, { month: "short", year: "numeric" })}</span>
                          <span className="tabular">{money(row.approved_value, me?.organization.base_currency ?? "JPY")} · {row.approved_requests} requests</span>
                        </div>
                        <div className="h-2 overflow-hidden rounded bg-surface-2">
                          <div className="h-full rounded bg-primary" style={{ width: `${Math.max(3, Number(row.approved_value) / max * 100)}%` }} />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <EmptyState title="No approved spend in this period">Monthly totals appear from completed approved requests.</EmptyState>
              )}
            </Card>
          </div>}
        </section>
      )}
    </>
  );
}

