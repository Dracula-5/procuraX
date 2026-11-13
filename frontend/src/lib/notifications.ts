// In-app notifications as the API returns them, and their wording in the UI language. The server
// stores only the event type and its facts (ADR-012), so switching language re-renders old ones too.
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { date, money } from "./format";
import { useLabels } from "./labels";

export const EVENT_TYPES = [
  "approval.routed",
  "approval.escalated",
  "approval.delegated",
  "purchase_request.rejected",
  "purchase_request.approved",
  "purchase_order.sent",
  "invoice.mismatch",
  "payment.awaiting_approval",
  "contract.expiring",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export interface NotificationItem {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  link: string | null;
  read_at: string | null;
  created_at: string;
}

export interface NotificationPage {
  items: NotificationItem[];
  unread_count: number;
}

export interface NotificationPreferences {
  email_enabled: boolean;
  muted_events: string[];
  email_available: boolean;
  event_types: string[];
}

const str = (value: unknown) => (value === null || value === undefined ? "—" : String(value));

export function useNotificationText() {
  const { t, i18n } = useTranslation();
  const labels = useLabels();
  return useMemo(
    () => (n: Pick<NotificationItem, "event_type" | "payload">) => {
      const p = n.payload;
      const params = {
        number: str(p.number),
        title: str(p.title),
        amount: p.amount_value !== undefined ? money(str(p.amount_value), str(p.currency ?? "JPY")) : str(p.amount),
        role: p.role ? labels.role(str(p.role)) : "—",
        delegator: str(p.delegator),
        reason: str(p.reason),
        poNumber: str(p.po_number),
        invoiceNumber: str(p.invoice_number),
        matchStatus: p.match_status ? labels.matchStatus(str(p.match_status)) : "—",
        contractNumber: str(p.contract_number),
        vendor: str(p.vendor),
        endsOn: p.ends_on ? date(str(p.ends_on)) : "—",
        daysLeft: str(p.days_left),
      };
      const base = `notifications.events.${n.event_type}`;
      if (!i18n.exists(`${base}.title`)) return { title: n.event_type, body: "" };
      return { title: String(t(`${base}.title` as never, params as never)), body: String(t(`${base}.body` as never, params as never)) };
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, i18n, i18n.language, labels],
  );
}
