import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Siren } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge, EmptyState, ErrorNotice, PageHeader, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { CATEGORIES, label, money, relative, ROLE_LABELS } from "../lib/format";
import type { InboxItem } from "../lib/types";

export function Inbox() {
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => api.get<InboxItem[]>("/approvals/inbox"), refetchInterval: 30_000 });
  return (
    <>
      <PageHeader
        title="Approval inbox"
        subtitle="Steps waiting on you — assigned to you by the org structure, or to a team you belong to. Most urgent first."
      />
      <ErrorNotice error={inbox.error} />
      {inbox.isLoading ? (
        <Spinner />
      ) : !inbox.data?.length ? (
        <EmptyState title="Nothing to approve">New steps appear here as soon as policy routes a request to you.</EmptyState>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Request</Th>
              <Th>Why you</Th>
              <Th className="text-right">Amount</Th>
              <Th>Due</Th>
            </tr>
          </thead>
          <tbody>
            {inbox.data.map((i) => (
              <tr key={i.approval_id} className="hover:bg-surface-2">
                <Td>
                  <Link to={`/app/requests/${i.purchase_request_id}`} className="font-medium hover:text-primary">
                    {i.title}
                  </Link>
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                    {i.number} · {i.requester_name} · {i.department_name} · {label(CATEGORIES, i.category)}
                    {i.is_emergency && (
                      <span className="inline-flex items-center gap-0.5 text-warn">
                        <Siren className="size-3" /> emergency
                      </span>
                    )}
                  </div>
                </Td>
                <Td>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone="primary">{label(ROLE_LABELS, i.approver_role)}</Badge>
                    <Badge tone={i.assigned_to_me ? "info" : "neutral"}>{i.assigned_to_me ? "assigned to you" : "team queue"}</Badge>
                  </div>
                  <ul className="mt-1 space-y-0.5 text-xs text-muted">
                    {i.reasons.map((r, idx) => (
                      <li key={idx}>
                        <span className="font-mono">{i.rule_ids[idx]}</span> {r}
                      </li>
                    ))}
                  </ul>
                </Td>
                <Td className="tabular text-right font-medium">{money(i.estimated_total, i.currency)}</Td>
                <Td className="whitespace-nowrap">
                  <span className={i.is_overdue ? "inline-flex items-center gap-1 text-warn" : "text-muted"}>
                    {i.is_overdue && <AlertTriangle className="size-4" />}
                    {relative(i.due_at)}
                  </span>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}

