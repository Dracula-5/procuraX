import { Badge, type Tone } from "./ui";
import { label, ROLE_LABELS, STATUS_LABELS } from "../lib/format";

const PR_TONES: Record<string, Tone> = {
  draft: "neutral",
  pending_approval: "info",
  policy_blocked: "danger",
  approved: "ok",
  rejected: "danger",
  cancelled: "neutral",
};

export const PRStatusBadge = ({ status }: { status: string }) => (
  <Badge tone={PR_TONES[status] ?? "neutral"}>{label(STATUS_LABELS, status)}</Badge>
);

const STEP_TONES: Record<string, Tone> = {
  waiting: "neutral",
  pending: "info",
  approved: "ok",
  rejected: "danger",
  skipped: "neutral",
  cancelled: "neutral",
};

export const StepStatusBadge = ({ status }: { status: string }) => (
  <Badge tone={STEP_TONES[status] ?? "neutral"}>{status}</Badge>
);

const VENDOR_TONES: Record<string, Tone> = { approved: "ok", pending_review: "warn", suspended: "danger", blocked: "danger" };
const RISK_TONES: Record<string, Tone> = { low: "ok", medium: "warn", high: "danger", unknown: "neutral" };
const CONTRACT_TONES: Record<string, Tone> = { active: "ok", expired: "warn", none: "neutral" };

export const VendorStatusBadge = ({ status }: { status: string }) => (
  <Badge tone={VENDOR_TONES[status] ?? "neutral"}>{status.replace("_", " ")}</Badge>
);
export const RiskBadge = ({ level }: { level: string }) => <Badge tone={RISK_TONES[level] ?? "neutral"}>risk: {level}</Badge>;
export const ContractBadge = ({ status }: { status: string }) => (
  <Badge tone={CONTRACT_TONES[status] ?? "neutral"}>contract: {status}</Badge>
);

export const RoleBadge = ({ role }: { role: string }) => <Badge tone="primary">{label(ROLE_LABELS, role)}</Badge>;

const SEVERITY_TONES: Record<string, Tone> = { block: "danger", route: "info", flag: "warn", info: "ok" };
export const SeverityBadge = ({ severity }: { severity: string }) => (
  <Badge tone={SEVERITY_TONES[severity] ?? "neutral"}>{severity}</Badge>
);

