import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Plus, Siren } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { PRStatusBadge } from "../components/badges";
import { EmptyState, ErrorNotice, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { CATEGORIES, date, label, money, STATUS_LABELS } from "../lib/format";
import type { Page, PRSummary } from "../lib/types";

const PAGE_SIZE = 25;

export function Requests() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const mine = params.get("mine") === "1";
  const status = params.get("status") ?? "";
  const category = params.get("category") ?? "";
  const q = params.get("q") ?? "";
  const page = Number(params.get("page") ?? "0");

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("page"); // any filter change returns to the first page
    setParams(next, { replace: true });
  };
  const goToPage = (n: number) => {
    const next = new URLSearchParams(params);
    if (n > 0) next.set("page", String(n));
    else next.delete("page");
    setParams(next, { replace: true });
  };

  // Debounce free-text search so typing doesn't fire a query per keystroke.
  const [search, setSearch] = useState(q);
  useEffect(() => {
    if (search === q) return;
    const t = setTimeout(() => update("q", search), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const query = useQuery({
    queryKey: ["requests", { mine, status, category, q, page }],
    queryFn: () =>
      api.get<Page<PRSummary>>("/purchase-requests", {
        mine,
        status: status || undefined,
        category: category || undefined,
        q: q || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  });

  const total = query.data?.total ?? 0;
  return (
    <>
      <PageHeader
        title="Purchase requests"
        subtitle="Requests you can see based on your role: your own, your team's, departments you head, or all."
        actions={
          can("purchase_request:create") && (
            <Link to="/app/requests/new" className="inline-flex h-10 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-fg hover:bg-primary-hover">
              <Plus className="size-4" /> New request
            </Link>
          )
        }
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Input placeholder="Search title or number" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search" />
        <Select value={status} onChange={(e) => update("status", e.target.value)} aria-label="Status">
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </Select>
        <Select value={category} onChange={(e) => update("category", e.target.value)} aria-label="Category">
          <option value="">All categories</option>
          {Object.entries(CATEGORIES).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </Select>
        <Select value={mine ? "1" : ""} onChange={(e) => update("mine", e.target.value)} aria-label="Scope">
          <option value="">Everything I can see</option>
          <option value="1">Only my requests</option>
        </Select>
      </div>
      <ErrorNotice error={query.error} />
      {query.isLoading ? (
        <Spinner />
      ) : !query.data?.items.length ? (
        <EmptyState title="No requests match these filters" />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Request</Th>
                <Th>Requester</Th>
                <Th>Category</Th>
                <Th className="text-right">Amount</Th>
                <Th>Status</Th>
                <Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((pr) => (
                <tr key={pr.id} className="cursor-pointer hover:bg-surface-2" onClick={() => navigate(`/app/requests/${pr.id}`)}>
                  <Td>
                    <Link to={`/app/requests/${pr.id}`} className="font-medium hover:text-primary" onClick={(e) => e.stopPropagation()}>
                      {pr.title}
                    </Link>
                    <div className="flex items-center gap-1.5 text-xs text-muted">
                      {pr.number}
                      {pr.is_emergency && (
                        <span className="inline-flex items-center gap-0.5 text-warn">
                          <Siren className="size-3" /> emergency
                        </span>
                      )}
                    </div>
                  </Td>
                  <Td>
                    {pr.requester_name}
                    <div className="text-xs text-muted">{pr.department_name}</div>
                  </Td>
                  <Td>{label(CATEGORIES, pr.category)}</Td>
                  <Td className="tabular text-right">{money(pr.estimated_total, pr.currency)}</Td>
                  <Td>
                    <PRStatusBadge status={pr.status} />
                  </Td>
                  <Td className="whitespace-nowrap text-muted">{date(pr.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <div className="mt-3 flex items-center justify-between text-sm text-muted">
            <span>
              {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
            </span>
            <span className="flex gap-2">
              <button className="rounded-md px-2 py-1 hover:bg-surface-2 disabled:opacity-40" disabled={page === 0} onClick={() => goToPage(page - 1)}>
                Previous
              </button>
              <button
                className="rounded-md px-2 py-1 hover:bg-surface-2 disabled:opacity-40"
                disabled={(page + 1) * PAGE_SIZE >= total}
                onClick={() => goToPage(page + 1)}
              >
                Next
              </button>
            </span>
          </div>
        </>
      )}
    </>
  );
}

