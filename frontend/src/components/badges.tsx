import { useTranslation } from "react-i18next";
import { useLabels } from "../lib/labels";
import { Badge, type Tone } from "./ui";

const PR_TONES: Record<string, Tone> = {
  draft: "neutral",
  pending_approval: "info",
  policy_blocked: "danger",
  approved: "ok",
  rejected: "danger",
  cancelled: "neutral",
};

export function PRStatusBadge({ status }: { status: string }) {
  const labels = useLabels();
  return <Badge tone={PR_TONES[status] ?? "neutral"}>{labels.prStatus(status)}</Badge>;
}

const STEP_TONES: Record<string, Tone> = {
  waiting: "neutral",
  pending: "info",
  approved: "ok",
  rejected: "danger",
  skipped: "neutral",
  cancelled: "neutral",
};

export function StepStatusBadge({ status }: { status: string }) {
  const labels = useLabels();
  return <Badge tone={STEP_TONES[status] ?? "neutral"}>{labels.stepStatus(status)}</Badge>;
}

const VENDOR_TONES: Record<string, Tone> = { approved: "ok", pending_review: "warn", suspended: "danger", blocked: "danger" };
const RISK_TONES: Record<string, Tone> = { low: "ok", medium: "warn", high: "danger", unknown: "neutral" };
const CONTRACT_TONES: Record<string, Tone> = { active: "ok", expired: "warn", none: "neutral" };

export function VendorStatusBadge({ status }: { status: string }) {
  const labels = useLabels();
  return <Badge tone={VENDOR_TONES[status] ?? "neutral"}>{labels.vendorStatus(status)}</Badge>;
}

export function RiskBadge({ level }: { level: string }) {
  const { t } = useTranslation();
  const labels = useLabels();
  return <Badge tone={RISK_TONES[level] ?? "neutral"}>{t("badges.risk", { level: labels.risk(level) })}</Badge>;
}

export function ContractBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  const labels = useLabels();
  return <Badge tone={CONTRACT_TONES[status] ?? "neutral"}>{t("badges.contract", { status: labels.contract(status) })}</Badge>;
}

export function RoleBadge({ role }: { role: string }) {
  const labels = useLabels();
  return <Badge tone="primary">{labels.role(role)}</Badge>;
}

const SEVERITY_TONES: Record<string, Tone> = { block: "danger", route: "info", flag: "warn", info: "ok" };

export function SeverityBadge({ severity }: { severity: string }) {
  const labels = useLabels();
  return <Badge tone={SEVERITY_TONES[severity] ?? "neutral"}>{labels.severity(severity)}</Badge>;
}
