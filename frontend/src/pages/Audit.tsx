import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, EmptyState, ErrorNotice, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import type { AuditEntry, Page } from "../lib/types";

const ENTITY_TYPES = ["purchase_request", "vendor", "budget", "user", "invitation", "approval_policy", "department", "cost_center", "organization"];
const PAGE = 50;

export function Audit() {
  const [entity, setEntity] = useState("");
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const logs = useQuery({
    queryKey: ["audit", entity, action, offset],
    queryFn: () => api.get<Page<AuditEntry>>("/audit-logs", { entity_type: entity || undefined, action: action || undefined, limit: PAGE, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader
        title="Audit trail"
        subtitle="Append-only: the application's database role can insert and read entries, never change or delete them."
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <Select value={entity} onChange={(e) => (setEntity(e.target.value), setOffset(0))} aria-label="Entity type">
          <option value="">All entities</option>
          {ENTITY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace("_", " ")}
            </option>
          ))}
        </Select>
        <Input placeholder="Action prefix, e.g. approval." value={action} onChange={(e) => (setAction(e.target.value), setOffset(0))} aria-label="Action" />
      </div>
      <ErrorNotice error={logs.error} />
      {logs.isLoading ? (
        <Spinner />
      ) : !logs.data?.items.length ? (
        <EmptyState title="No audit entries match" />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>When</Th>
                <Th>Actor</Th>
                <Th>Event</Th>
                <Th>Request ID</Th>
              </tr>
            </thead>
            <tbody>
              {logs.data.items.map((e) => (
                <tr key={e.id}>
                  <Td className="whitespace-nowrap text-muted">{dateTime(e.created_at)}</Td>
                  <Td>
                    {e.actor_type === "user" ? e.actor_email : <Badge tone="primary">{e.actor_type.replace("_", " ")}</Badge>}
                  </Td>
                  <Td>
                    <p>
                      {e.entity_type === "purchase_request" && e.entity_id ? (
                        <Link to={`/app/requests/${e.entity_id}`} className="hover:text-primary">
                          {e.summary}
                        </Link>
                      ) : (
                        e.summary
                      )}
                    </p>
                    <p className="font-mono text-xs text-muted">{e.action}</p>
                  </Td>
                  <Td className="font-mono text-xs text-muted">{e.request_id?.slice(0, 12) ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <div className="mt-3 flex items-center justify-between text-sm text-muted">
            <span>
              {offset + 1}–{Math.min(offset + PAGE, logs.data.total)} of {logs.data.total}
            </span>
            <span className="flex gap-2">
              <button className="rounded-md px-2 py-1 hover:bg-surface-2 disabled:opacity-40" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE))}>
                Previous
              </button>
              <button className="rounded-md px-2 py-1 hover:bg-surface-2 disabled:opacity-40" disabled={offset + PAGE >= logs.data.total} onClick={() => setOffset((o) => o + PAGE)}>
                Next
              </button>
            </span>
          </div>
        </>
      )}
    </>
  );
}

