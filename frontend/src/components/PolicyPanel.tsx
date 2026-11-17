import clsx from "clsx";
import { Ban, CheckCircle2, GitBranch } from "lucide-react";
import { useTranslation } from "react-i18next";
import { money, percent } from "../lib/format";
import { useLabels } from "../lib/labels";
import type { PolicyEvaluation } from "../lib/types";
import { SeverityBadge } from "./badges";

const OUTCOME = {
  blocked: { icon: Ban, tone: "border-danger/30 bg-danger-soft text-danger" },
  auto_approved: { icon: CheckCircle2, tone: "border-ok/30 bg-ok-soft text-ok" },
  requires_approval: { icon: GitBranch, tone: "border-info/30 bg-info-soft text-info" },
} as const;

/** Explains a deterministic policy decision: outcome, every rule that fired, and budget position. */
export function PolicyPanel({ evaluation, currency, preview }: { evaluation: PolicyEvaluation; currency: string; preview?: boolean }) {
  const { t } = useTranslation();
  const labels = useLabels();
  const o = OUTCOME[evaluation.outcome];
  const b = evaluation.budget;
  const utilization = Number(b.utilization_after ?? 0);
  return (
    <div className="space-y-4">
      <div className={clsx("flex items-start gap-3 rounded-md border px-3 py-2.5", o.tone)}>
        <o.icon className="mt-0.5 size-5 shrink-0" />
        <div className="text-sm">
          <p className="font-semibold">
            {preview ? t("policyPanel.ifSubmitted") : ""}
            {t(`policyPanel.outcome.${evaluation.outcome}`)}
          </p>
          {evaluation.outcome === "requires_approval" && (
            <p className="mt-0.5">{evaluation.required_approvals.map((s) => labels.role(s.role)).join(" → ")}</p>
          )}
          <p className="mt-0.5 text-xs opacity-80">
            {t("policyPanel.versions", { policy: evaluation.policy_version, engine: evaluation.engine_version })}
          </p>
        </div>
      </div>

      <ul className="space-y-2">
        {evaluation.hits.map((h, i) => (
          <li key={`${h.rule_id}-${i}`} className="rounded-md border border-border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-muted">{h.rule_id}</span>
              <span className="font-medium">{labels.ruleTitle(h.rule_id, h.title)}</span>
              <SeverityBadge severity={h.severity} />
            </div>
            <p className="mt-1 text-muted">{labels.ruleMessage(h, currency)}</p>
          </li>
        ))}
      </ul>

      {b.status !== "not_applicable" && (
        <div className="rounded-md border border-border p-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="font-medium">{t("policyPanel.budgetFy", { year: b.fiscal_year })}</span>
            <span className={clsx("text-xs font-medium", b.status === "exceeded" ? "text-danger" : b.status === "near_limit" ? "text-warn" : "text-muted")}>
              {t(`policyPanel.budgetStatus.${b.status}`)}
            </span>
          </div>
          {b.budget_amount && (
            <>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuenow={Math.round(utilization * 100)} aria-valuemin={0} aria-valuemax={100}>
                <div
                  className={clsx("h-full rounded-full", utilization > 1 ? "bg-danger" : utilization >= 0.9 ? "bg-warn" : "bg-primary")}
                  style={{ width: `${Math.min(utilization, 1) * 100}%` }}
                />
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted">
                <dt>{t("policyPanel.budget")}</dt>
                <dd className="tabular text-right">{money(b.budget_amount, currency)}</dd>
                <dt>{t("policyPanel.committed")}</dt>
                <dd className="tabular text-right">{money(b.committed, currency)}</dd>
                <dt>{t("policyPanel.thisRequest")}</dt>
                <dd className="tabular text-right">{money(b.requested, currency)}</dd>
                <dt>{t("policyPanel.remainingAfter")}</dt>
                <dd className="tabular text-right">{money(b.remaining_after, currency)}</dd>
                <dt>{t("policyPanel.utilisationAfter")}</dt>
                <dd className="tabular text-right">{percent(b.utilization_after)}</dd>
              </dl>
            </>
          )}
        </div>
      )}
    </div>
  );
}
