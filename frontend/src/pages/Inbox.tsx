import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Siren, UserRoundCog } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Badge, EmptyState, ErrorNotice, PageHeader, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { money, relative } from "../lib/format";
import { useLabels } from "../lib/labels";
import type { InboxItem } from "../lib/types";

export function Inbox() {
  const { t } = useTranslation();
  const labels = useLabels();
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => api.get<InboxItem[]>("/approvals/inbox"), refetchInterval: 30_000 });
  return (
    <>
      <PageHeader
        title={t("inbox.title")}
        subtitle={t("inbox.subtitle")}
        actions={
          <Link to="/app/approvals/delegations" className="inline-flex h-10 items-center gap-2 rounded-md border border-border bg-surface px-4 text-sm font-medium hover:bg-surface-2">
            <UserRoundCog className="size-4" /> {t("inbox.delegations")}
          </Link>
        }
      />
      <ErrorNotice error={inbox.error} />
      {inbox.isLoading ? (
        <Spinner />
      ) : !inbox.data?.length ? (
        <EmptyState title={t("inbox.emptyTitle")}>{t("inbox.emptyBody")}</EmptyState>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t("requests.col.request")}</Th>
              <Th>{t("inbox.whyYou")}</Th>
              <Th className="text-right">{t("common.amount")}</Th>
              <Th>{t("common.due")}</Th>
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
                    {i.number} · {i.requester_name} · {i.department_name} · {labels.category(i.category)}
                    {i.is_emergency && (
                      <span className="inline-flex items-center gap-0.5 text-warn">
                        <Siren className="size-3" /> {t("requests.emergency")}
                      </span>
                    )}
                  </div>
                </Td>
                <Td>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone="primary">{labels.role(i.approver_role)}</Badge>
                    <Badge tone={i.assigned_to_me ? "info" : "neutral"}>{i.assigned_to_me ? t("inbox.assignedToYou") : t("inbox.teamQueue")}</Badge>
                  </div>
                  <ul className="mt-1 space-y-0.5 text-xs text-muted">
                    {i.reasons.map((r, idx) => (
                      <li key={idx}>
                        <span className="font-mono">{i.rule_ids[idx]}</span> {labels.stepReason(i.rule_ids[idx], r)}
                      </li>
                    ))}
                  </ul>
                </Td>
                <Td className="tabular text-right font-medium">{money(i.estimated_total, i.currency)}</Td>
                <Td className="whitespace-nowrap">
                  <span className={i.is_overdue ? "inline-flex items-center gap-1 text-warn" : "text-muted"}>
                    {i.is_overdue && <AlertTriangle className="size-4" aria-label={t("common.pastSla")} />}
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
