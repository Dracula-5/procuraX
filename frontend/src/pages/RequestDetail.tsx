import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Bot, Pencil, Siren, User } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { PRStatusBadge, StepStatusBadge } from "../components/badges";
import { PolicyPanel } from "../components/PolicyPanel";
import { Button, Card, ErrorNotice, Field, PageHeader, Spinner, Table, Td, Textarea, Th } from "../components/ui";
import { api } from "../lib/api";
import { date, dateTime, money, relative } from "../lib/format";
import { useLabels } from "../lib/labels";
import type { ApprovalStep, AuditEntry, PolicyEvaluation, PRDetail } from "../lib/types";

type Action = "approve" | "reject" | "cancel";

const ACTION_STYLE: Record<Action, { required: boolean; variant: "primary" | "danger" }> = {
  approve: { required: false, variant: "primary" },
  reject: { required: true, variant: "danger" },
  cancel: { required: true, variant: "danger" },
};

export function RequestDetail() {
  const { t } = useTranslation();
  const labels = useLabels();
  const { id } = useParams();
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Action | null>(null);
  const [comment, setComment] = useState("");
  const [preview, setPreview] = useState<PolicyEvaluation | null>(null);

  const pr = useQuery({ queryKey: ["request", id], queryFn: () => api.get<PRDetail>(`/purchase-requests/${id}`) });
  const timeline = useQuery({ queryKey: ["request", id, "timeline"], queryFn: () => api.get<AuditEntry[]>(`/purchase-requests/${id}/timeline`) });

  const refresh = (data: PRDetail) => {
    qc.setQueryData(["request", id], data);
    qc.invalidateQueries({ queryKey: ["request", id, "timeline"] });
    qc.invalidateQueries({ queryKey: ["requests"] });
    qc.invalidateQueries({ queryKey: ["inbox"] });
    qc.invalidateQueries({ queryKey: ["pr-stats"] });
  };

  const transition = useMutation({
    mutationFn: ({ action, body }: { action: string; body?: object }) =>
      action === "submit" ? api.patch<PRDetail>(`/purchase-requests/${id}/submit`) : api.post<PRDetail>(`/purchase-requests/${id}/${action}`, body),
    onSuccess: (data) => {
      refresh(data);
      setDialog(null);
      setComment("");
      setPreview(null);
    },
  });
  const previewPolicy = useMutation({
    mutationFn: () => api.post<PolicyEvaluation>(`/purchase-requests/${id}/policy-preview`),
    onSuccess: setPreview,
  });

  if (pr.isLoading) return <Spinner />;
  if (!pr.data) return <ErrorNotice error={pr.error} />;
  const r = pr.data;
  const can = (a: string) => r.allowed_actions.includes(a);
  const currentRound = r.approvals.filter((s) => s.round === r.submission_count);
  const pastRounds = [...new Set(r.approvals.map((s) => s.round))].filter((n) => n !== r.submission_count).sort((a, b) => b - a);

  const confirm = () => {
    if (!dialog) return;
    const body = dialog === "cancel" ? { reason: comment } : { comment: comment || null };
    transition.mutate({ action: dialog, body });
  };

  return (
    <>
      <PageHeader
        title={r.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{r.number}</span>
            <PRStatusBadge status={r.status} />
            {r.is_emergency && (
              <span className="inline-flex items-center gap-1 text-warn">
                <Siren className="size-4" /> {t("requestDetail.emergency")}
              </span>
            )}
          </span>
        }
        actions={
          <>
            {can("edit") && (
              <Link to={`/app/requests/${r.id}/edit`} className="inline-flex h-10 items-center gap-2 rounded-md border border-border bg-surface px-4 text-sm font-medium hover:bg-surface-2">
                <Pencil className="size-4" /> {t("common.edit")}
              </Link>
            )}
            {can("submit") && (
              <Button onClick={() => transition.mutate({ action: "submit" })} loading={transition.isPending && !dialog}>
                {t("requestDetail.submit")}
              </Button>
            )}
            {can("reopen") && (
              <Button variant="secondary" onClick={() => transition.mutate({ action: "reopen" })} loading={transition.isPending && !dialog}>
                {t("requestDetail.reopen")}
              </Button>
            )}
            {can("approve") && <Button onClick={() => setDialog("approve")}>{t("requestDetail.approve")}</Button>}
            {can("reject") && (
              <Button variant="danger" onClick={() => setDialog("reject")}>
                {t("requestDetail.reject")}
              </Button>
            )}
            {can("cancel") && (
              <Button variant="ghost" onClick={() => setDialog("cancel")}>
                {t("requestDetail.cancelRequest")}
              </Button>
            )}
          </>
        }
      />

      {!dialog && <ErrorNotice error={transition.error} />}

      {dialog && (
        <Card title={t(`requestDetail.dialog.${dialog}.title`)} className="mb-6">
          <Field label={t(`requestDetail.dialog.${dialog}.field`)}>
            <Textarea autoFocus value={comment} onChange={(e) => setComment(e.target.value)} className="min-h-20" />
          </Field>
          <div className="mt-3">
            <ErrorNotice error={transition.error} />
          </div>
          <div className="mt-3 flex gap-2">
            <Button
              variant={ACTION_STYLE[dialog].variant}
              onClick={confirm}
              loading={transition.isPending}
              disabled={ACTION_STYLE[dialog].required && comment.trim().length < 3}
            >
              {t(`requestDetail.dialog.${dialog}.button`)}
            </Button>
            <Button variant="ghost" onClick={() => setDialog(null)}>
              {t("common.back")}
            </Button>
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <div className="space-y-6">
          <Card title={t("requestDetail.request")}>
            <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
              <Detail label={t("common.amount")} value={<span className="tabular text-base font-semibold">{money(r.estimated_total, r.currency)}</span>} />
              <Detail label={t("common.category")} value={labels.category(r.category)} />
              <Detail label={t("requestForm.neededBy")} value={date(r.required_by)} />
              <Detail label={t("requests.col.requester")} value={r.requester_name} />
              <Detail label={t("requestDetail.chargedTo")} value={`${r.cost_center_code ?? "—"} · ${r.department_name ?? "—"}`} />
              <Detail label={t("requestForm.preferredVendor")} value={r.vendor_name ?? t("requestDetail.notSpecified")} />
              <Detail label={t("requestDetail.submitted")} value={dateTime(r.submitted_at)} />
              <Detail label={t("requestDetail.decided")} value={dateTime(r.decided_at)} />
              <Detail label={t("requestDetail.fiscalYear")} value={r.fiscal_year ? t("common.fy", { year: r.fiscal_year }) : "—"} />
            </dl>
            {r.justification && (
              <div className="mt-4 border-t border-border pt-3 text-sm">
                <p className="text-xs font-medium uppercase tracking-wide text-muted">{t("requestDetail.justification")}</p>
                <p className="mt-1 whitespace-pre-line">{r.justification}</p>
              </div>
            )}
            {r.cancel_reason && (
              <p className="mt-3 rounded-md bg-surface-2 px-3 py-2 text-sm">
                <span className="font-medium">{t("requestDetail.cancelled")}</span> {r.cancel_reason}
              </p>
            )}
          </Card>

          <Card title={t("requestDetail.lines", { count: r.items.length })}>
            {r.items.length ? (
              <Table>
                <thead>
                  <tr>
                    <Th>#</Th>
                    <Th>{t("requestForm.description")}</Th>
                    <Th className="text-right">{t("common.qty")}</Th>
                    <Th className="text-right">{t("common.unitPrice")}</Th>
                    <Th className="text-right">{t("common.total")}</Th>
                  </tr>
                </thead>
                <tbody>
                  {r.items.map((i) => (
                    <tr key={i.id}>
                      <Td className="text-muted">{i.line_no}</Td>
                      <Td>{i.description}</Td>
                      <Td className="tabular text-right">
                        {Number(i.quantity)} {i.uom}
                      </Td>
                      <Td className="tabular text-right">{money(i.unit_price, r.currency)}</Td>
                      <Td className="tabular text-right font-medium">{money(i.line_total, r.currency)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : (
              <p className="text-sm text-muted">{t("requestDetail.noLines")}</p>
            )}
          </Card>

          <Card title={t("requestDetail.chain")}>
            {currentRound.length ? (
              <Steps steps={currentRound} />
            ) : (
              <p className="text-sm text-muted">{r.status === "draft" ? t("requestDetail.chainDraft") : t("requestDetail.chainNone")}</p>
            )}
            {pastRounds.map((round) => (
              <details key={round} className="mt-4 border-t border-border pt-3">
                <summary className="cursor-pointer text-sm text-muted">{t("requestDetail.history", { round })}</summary>
                <div className="mt-3">
                  <Steps steps={r.approvals.filter((s) => s.round === round)} />
                </div>
              </details>
            ))}
          </Card>

          <Card title={t("requestDetail.timeline")}>
            <Timeline entries={timeline.data ?? []} />
          </Card>
        </div>

        <div className="space-y-6">
          <Card
            title={t("requestDetail.policyDecision")}
            actions={
              r.status === "draft" && (
                <Button size="sm" variant="secondary" onClick={() => previewPolicy.mutate()} loading={previewPolicy.isPending}>
                  {t("requestDetail.preview")}
                </Button>
              )
            }
          >
            <ErrorNotice error={previewPolicy.error} />
            {preview ? (
              <PolicyPanel evaluation={preview} currency={r.currency} preview />
            ) : r.policy_evaluation && r.status !== "draft" ? (
              <PolicyPanel evaluation={r.policy_evaluation} currency={r.currency} />
            ) : (
              <p className="text-sm text-muted">{t("requestDetail.notEvaluated")}</p>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function Detail({ label: l, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted">{l}</dt>
      <dd className="mt-0.5">{value ?? "—"}</dd>
    </div>
  );
}

function Steps({ steps }: { steps: ApprovalStep[] }) {
  const { t } = useTranslation();
  const labels = useLabels();
  return (
    <ol className="space-y-3">
      {steps.map((s, idx) => (
        <li key={s.id} className="flex gap-3">
          <div className="flex flex-col items-center">
            <span
              className={clsx(
                "flex size-7 items-center justify-center rounded-full text-xs font-semibold",
                s.status === "approved" ? "bg-ok-soft text-ok" : s.status === "rejected" ? "bg-danger-soft text-danger" : s.status === "pending" ? "bg-info-soft text-info" : "bg-surface-2 text-muted",
              )}
            >
              {idx + 1}
            </span>
            {idx < steps.length - 1 && <span className="mt-1 w-px flex-1 bg-border" />}
          </div>
          <div className="min-w-0 flex-1 pb-1 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{labels.role(s.approver_role)}</span>
              <StepStatusBadge status={s.status} />
              {s.is_overdue && (
                <span className="inline-flex items-center gap-1 text-xs text-warn">
                  <AlertTriangle className="size-3" /> {t("common.pastSla")}
                </span>
              )}
            </div>
            <p className="text-xs text-muted">
              {s.assigned_user_name ? t("requestDetail.assignedTo", { name: s.assigned_user_name }) : t("requestDetail.anyRole", { role: labels.role(s.approver_role) })}
              {s.status === "pending" && s.due_at && t("requestDetail.due", { when: relative(s.due_at) })}
              {s.decided_by_name && t("requestDetail.decidedBy", { status: labels.stepStatus(s.status), name: s.decided_by_name, when: relative(s.decided_at) })}
            </p>
            <ul className="mt-1 space-y-0.5 text-xs text-muted">
              {s.reasons.map((reason, i) => (
                <li key={i}>
                  <span className="font-mono">{s.rule_ids[i]}</span> {labels.stepReason(s.rule_ids[i], reason)}
                </li>
              ))}
            </ul>
            {s.comment && <p className="mt-1 rounded bg-surface-2 px-2 py-1 text-xs">“{s.comment}”</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Timeline({ entries }: { entries: AuditEntry[] }) {
  const { t } = useTranslation();
  const labels = useLabels();
  if (!entries.length) return <p className="text-sm text-muted">{t("requestDetail.noEvents")}</p>;
  return (
    <ol className="space-y-3">
      {entries.map((e) => {
        const machine = e.actor_type === "policy_engine" || e.actor_type === "system";
        return (
          <li key={e.id} className="flex gap-3 text-sm">
            <span className={clsx("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full", machine ? "bg-primary-soft text-primary" : "bg-surface-2 text-muted")}>
              {machine ? <Bot className="size-3.5" /> : <User className="size-3.5" />}
            </span>
            <div className="min-w-0">
              <p>{labels.auditSummary(e.action, e.summary)}</p>
              <p className="text-xs text-muted">
                {machine ? t("requestDetail.policyEngine") : e.actor_email} · {dateTime(e.created_at)} · <span className="font-mono">{e.action}</span>
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
