import clsx from "clsx";
import { Ban, CheckCircle2, GitBranch } from "lucide-react";
import { label, money, percent, ROLE_LABELS } from "../lib/format";
import type { PolicyEvaluation } from "../lib/types";
import { SeverityBadge } from "./badges";

const OUTCOME = {
  blocked: { icon: Ban, tone: "border-danger/30 bg-danger-soft text-danger", title: "Blocked by policy" },
  auto_approved: { icon: CheckCircle2, tone: "border-ok/30 bg-ok-soft text-ok", title: "Auto-approved by policy" },
  requires_approval: { icon: GitBranch, tone: "border-info/30 bg-info-soft text-info", title: "Human approval required" },
} as const;

const BUDGET_LABEL: Record<string, string> = {
  within_budget: "Within budget",
  near_limit: "Near budget limit",
  exceeded: "Exceeds budget",
  no_budget: "No budget defined",
  not_applicable: "Not checked",
};

/** Explains a deterministic policy decision: outcome, every rule that fired, and budget position. */
export function PolicyPanel({ evaluation, currency, preview }: { evaluation: PolicyEvaluation; currency: string; preview?: boolean }) {
  const o = OUTCOME[evaluation.outcome];
  const b = evaluation.budget;
  const utilization = Number(b.utilization_after ?? 0);
  return (
    <div className="space-y-4">
      <div className={clsx("flex items-start gap-3 rounded-md border px-3 py-2.5", o.tone)}>
        <o.icon className="mt-0.5 size-5 shrink-0" />
        <div className="text-sm">
          <p className="font-semibold">
            {preview ? "If submitted now: " : ""}
            {o.title}
          </p>
          {evaluation.outcome === "requires_approval" && (
            <p className="mt-0.5">{evaluation.required_approvals.map((s) => label(ROLE_LABELS, s.role)).join(" → ")}</p>
          )}
          <p className="mt-0.5 text-xs opacity-80">
            Policy v{evaluation.policy_version} · engine {evaluation.engine_version} · deterministic rules, no ML
          </p>
        </div>
      </div>

      <ul className="space-y-2">
        {evaluation.hits.map((h, i) => (
          <li key={`${h.rule_id}-${i}`} className="rounded-md border border-border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-muted">{h.rule_id}</span>
              <span className="font-medium">{h.title}</span>
              <SeverityBadge severity={h.severity} />
            </div>
            <p className="mt-1 text-muted">{h.message}</p>
          </li>
        ))}
      </ul>

      {b.status !== "not_applicable" && (
        <div className="rounded-md border border-border p-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="font-medium">Budget FY{b.fiscal_year}</span>
            <span className={clsx("text-xs font-medium", b.status === "exceeded" ? "text-danger" : b.status === "near_limit" ? "text-warn" : "text-muted")}>
              {BUDGET_LABEL[b.status]}
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
                <dt>Budget</dt>
                <dd className="tabular text-right">{money(b.budget_amount, currency)}</dd>
                <dt>Already committed</dt>
                <dd className="tabular text-right">{money(b.committed, currency)}</dd>
                <dt>This request</dt>
                <dd className="tabular text-right">{money(b.requested, currency)}</dd>
                <dt>Remaining after</dt>
                <dd className="tabular text-right">{money(b.remaining_after, currency)}</dd>
                <dt>Utilisation after</dt>
                <dd className="tabular text-right">{percent(b.utilization_after)}</dd>
              </dl>
            </>
          )}
        </div>
      )}
    </div>
  );
}

