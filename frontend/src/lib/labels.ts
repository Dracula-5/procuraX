// Translated labels for the API's machine values (statuses, roles, categories, rule IDs, audit
// actions). The API returns stable codes; the UI owns their wording in each language.
import type { i18n as I18n, TFunction } from "i18next";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { money, percent } from "./format";
import type { RuleHit } from "./types";

export const CATEGORY_KEYS = [
  "it_services",
  "travel",
  "facilities",
  "marketing",
  "professional_services",
  "hardware",
  "software",
  "logistics",
  "office_supplies",
  "manufacturing",
] as const;
export const PR_STATUS_KEYS = ["draft", "pending_approval", "policy_blocked", "approved", "rejected", "cancelled"] as const;

export type LabelGroup =
  | "category"
  | "role"
  | "prStatus"
  | "stepStatus"
  | "vendorStatus"
  | "risk"
  | "contract"
  | "severity"
  | "poStatus"
  | "matchStatus"
  | "paymentStatus"
  | "entity"
  | "actor";

const humanize = (key: string) => key.replace(/_/g, " ");

// Keys built at runtime cannot be checked by the typed `t`, so they go through this one helper,
// which falls back to the readable code when a value has no translation yet.
function dynamic(t: TFunction, i18n: I18n, key: string, fallback: string, params?: Record<string, unknown>): string {
  return i18n.exists(key) ? String(t(key as never, params as never)) : fallback;
}

const num = (value: unknown): string | null =>
  value === null || value === undefined || Array.isArray(value) || typeof value === "object" ? null : String(value);

export function makeLabels(t: TFunction, i18n: I18n) {
  const label = (group: LabelGroup, key: string | null | undefined) =>
    key ? dynamic(t, i18n, `labels.${group}.${key}`, humanize(key)) : "—";
  const english = () => !i18n.language?.startsWith("ja");

  const ruleTitle = (ruleId: string, fallback: string) => dynamic(t, i18n, `labels.rules.${ruleId}.title`, fallback);
  const ruleDescription = (ruleId: string, fallback: string) =>
    english() ? fallback : dynamic(t, i18n, `labels.rules.${ruleId}.description`, fallback);

  /** English shows the engine's own sentence; other languages rebuild it from the rule's evidence. */
  const ruleMessage = (hit: Pick<RuleHit, "rule_id" | "message" | "evidence">, currency: string) => {
    if (english()) return hit.message;
    const e = hit.evidence ?? {};
    const m = (value: unknown) => {
      const n = num(value);
      return n === null ? "—" : money(n, currency);
    };
    const related: string[] = Array.isArray(e.related_requests) ? e.related_requests.map(String) : [];
    const budget = num(e.budget);
    const committed = num(e.committed);
    const requested = num(e.requested);
    const over = budget !== null && committed !== null && requested !== null ? m(String(Number(requested) - (Number(budget) - Number(committed)))) : "—";
    return dynamic(t, i18n, `labels.rules.${hit.rule_id}.message`, hit.message, {
      amount: m(e.amount),
      threshold: m(e.threshold),
      combined: m(e.combined_amount),
      over,
      utilization: num(e.utilization_after) === null ? "—" : percent(num(e.utilization_after)),
      year: e.fiscal_year ?? "",
      chars: e.justification_chars ?? 0,
      required: e.required_chars ?? 0,
      requestCurrency: e.request_currency ?? "",
      policyCurrency: e.policy_currency ?? "",
      vendorStatus: label("vendorStatus", num(e.vendor_status)),
      contractStatus: label("contract", num(e.contract_status)),
      category: label("category", num(e.category)),
      days: e.window_days ?? "",
      count: related.length,
      related: related.join(", "),
    });
  };

  return {
    category: (key: string) => label("category", key),
    role: (key: string) => label("role", key),
    prStatus: (key: string) => label("prStatus", key),
    stepStatus: (key: string) => label("stepStatus", key),
    vendorStatus: (key: string) => label("vendorStatus", key),
    risk: (key: string) => label("risk", key),
    contract: (key: string) => label("contract", key),
    severity: (key: string) => label("severity", key),
    poStatus: (key: string) => label("poStatus", key),
    matchStatus: (key: string) => label("matchStatus", key),
    paymentStatus: (key: string) => label("paymentStatus", key),
    entity: (key: string) => label("entity", key),
    actor: (key: string) => label("actor", key),
    /** Role descriptions come from the API in English; other languages use their own wording. */
    roleDescription: (key: string, fallback: string) =>
      english() ? fallback : dynamic(t, i18n, `labels.roleDescription.${key}`, fallback),
    ruleTitle,
    ruleDescription,
    ruleMessage,
    /** Approval-step reasons are engine sentences; outside English the rule's title is shown. */
    stepReason: (ruleId: string | undefined, reason: string) => (english() || !ruleId ? reason : ruleTitle(ruleId, reason)),
    /** Audit summaries are English sentences written at the time; outside English the action is described. */
    auditSummary: (action: string, summary: string) => (english() ? summary : dynamic(t, i18n, `labels.audit.${action}`, summary)),
  };
}

export type Labels = ReturnType<typeof makeLabels>;

export function useLabels(): Labels {
  const { t, i18n } = useTranslation();
  // Recomputed when the language changes so every caller re-renders with the new wording.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => makeLabels(t, i18n), [t, i18n, i18n.language]);
}
