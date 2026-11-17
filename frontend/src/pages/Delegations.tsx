import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorNotice, PageHeader, Select, Spinner, Table, Td, Th, type Tone } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, money, relative } from "../lib/format";
import { delegationState, type Delegation, type DelegationState } from "../lib/delegations";
import { useLabels } from "../lib/labels";

const STATE_TONE: Record<DelegationState, Tone> = { active: "info", revoked: "neutral", completed: "ok" };

type DelegableStep = {
  approval_id: string;
  purchase_request_id: string;
  number: string;
  title: string;
  requester_name: string | null;
  approver_role: string;
  status: string;
  estimated_total: string;
  currency: string;
  due_at: string | null;
};
type Candidate = { id: string; full_name: string; job_title: string | null };
/** Hand one named approval step to a colleague with the same approver role, and take it back before it is decided. */
export function Delegations() {
  const { t } = useTranslation();
  const labels = useLabels();
  const qc = useQueryClient();
  const delegable = useQuery({ queryKey: ["delegable-steps"], queryFn: () => api.get<DelegableStep[]>("/approvals/delegable") });
  const delegations = useQuery({ queryKey: ["delegations"], queryFn: () => api.get<Delegation[]>("/approvals/delegations") });
  const refresh = () => {
    for (const key of ["delegable-steps", "delegations", "inbox", "pr-stats"]) void qc.invalidateQueries({ queryKey: [key] });
  };
  const revoke = useMutation({ mutationFn: (id: string) => api.post(`/approvals/delegations/${id}/revoke`), onSuccess: refresh });

  return (
    <>
      <PageHeader title={t("delegations.title")} subtitle={t("delegations.subtitle")} />
      <div className="space-y-6">
        <Card title={t("delegations.delegateTitle")}>
          <ul className="mb-4 list-disc space-y-0.5 pl-5 text-xs text-muted">
            {(["named", "role", "once", "revoke"] as const).map((rule) => (
              <li key={rule}>{t(`delegations.rules.${rule}`)}</li>
            ))}
          </ul>
          <ErrorNotice error={delegable.error} />
          {delegable.isLoading ? (
            <Spinner />
          ) : !delegable.data?.length ? (
            <EmptyState title={t("delegations.noneTitle")}>{t("delegations.noneBody")}</EmptyState>
          ) : (
            <ul className="divide-y divide-border">
              {delegable.data.map((step) => (
                <DelegableRow key={step.approval_id} step={step} onDone={refresh} />
              ))}
            </ul>
          )}
        </Card>

        <Card title={t("delegations.listTitle")}>
          <ErrorNotice error={delegations.error ?? revoke.error} />
          {delegations.isLoading ? (
            <Spinner />
          ) : !delegations.data?.length ? (
            <EmptyState title={t("delegations.emptyTitle")}>{t("delegations.emptyBody")}</EmptyState>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>{t("requests.col.request")}</Th>
                  <Th>{t("delegations.col.step")}</Th>
                  <Th>{t("delegations.col.fromTo")}</Th>
                  <Th>{t("delegations.col.delegated")}</Th>
                  <Th>{t("common.status")}</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {delegations.data.map((d) => {
                  const state = delegationState(d);
                  return (
                    <tr key={d.id}>
                      <Td>
                        <Link to={`/app/requests/${d.purchase_request_id}`} className="font-medium hover:text-primary">
                          {d.request_number}
                        </Link>
                        <p className="text-xs text-muted">{d.request_title}</p>
                      </Td>
                      <Td>{labels.role(d.approver_role)}</Td>
                      <Td>
                        <span className="inline-flex flex-wrap items-center gap-1">
                          {d.delegator_name} <ArrowRight className="size-3 text-muted" aria-label={t("delegations.to")} /> {d.delegate_name}
                        </span>
                      </Td>
                      <Td className="whitespace-nowrap text-muted">{dateTime(d.delegated_at)}</Td>
                      <Td>
                        <Badge tone={STATE_TONE[state]}>{t(`delegations.state.${state}`)}</Badge>
                        {d.revoked_at && <p className="mt-1 text-xs text-muted">{dateTime(d.revoked_at)}</p>}
                      </Td>
                      <Td>
                        {d.can_revoke && (
                          <Button size="sm" variant="secondary" loading={revoke.isPending && revoke.variables === d.id} onClick={() => revoke.mutate(d.id)}>
                            {t("delegations.revoke")}
                          </Button>
                        )}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}

function DelegableRow({ step, onDone }: { step: DelegableStep; onDone: () => void }) {
  const { t } = useTranslation();
  const labels = useLabels();
  const [open, setOpen] = useState(false);
  const [delegateId, setDelegateId] = useState("");
  // Candidates come from the API's own eligibility rule, so the list never offers someone it would refuse.
  const candidates = useQuery({
    queryKey: ["delegate-candidates", step.approval_id],
    queryFn: () => api.get<Candidate[]>(`/approvals/${step.approval_id}/delegate-candidates`),
    enabled: open,
  });
  const create = useMutation({
    mutationFn: () => api.post("/approvals/delegations", { approval_id: step.approval_id, delegate_user_id: delegateId }),
    onSuccess: onDone,
  });

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/app/requests/${step.purchase_request_id}`} className="font-medium hover:text-primary">
            {step.number} · {step.title}
          </Link>
          <p className="text-xs text-muted">
            {labels.role(step.approver_role)} · {labels.stepStatus(step.status)} · {step.requester_name} · {money(step.estimated_total, step.currency)}
            {step.due_at && ` · ${t("delegations.due", { when: relative(step.due_at) })}`}
          </p>
        </div>
        {!open && (
          <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
            {t("delegations.choose")}
          </Button>
        )}
      </div>
      {open && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          {candidates.isLoading ? (
            <Spinner label={t("delegations.loadingCandidates")} />
          ) : candidates.data?.length ? (
            <>
              <Select className="max-w-72" value={delegateId} onChange={(e) => setDelegateId(e.target.value)} aria-label={t("delegations.delegateTo", { number: step.number })}>
                <option value="">{t("delegations.pickDelegate")}</option>
                {candidates.data.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.full_name}
                    {c.job_title ? ` · ${c.job_title}` : ""}
                  </option>
                ))}
              </Select>
              <Button size="sm" loading={create.isPending} disabled={!delegateId} onClick={() => create.mutate()}>
                {t("delegations.delegate")}
              </Button>
            </>
          ) : (
            !candidates.error && <p className="text-sm text-muted">{t("delegations.noCandidates", { role: labels.role(step.approver_role) })}</p>
          )}
          <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
            {t("common.cancel")}
          </Button>
          <div className="w-full">
            <ErrorNotice error={candidates.error ?? create.error} />
          </div>
        </div>
      )}
    </li>
  );
}
