const ZERO_DECIMAL = new Set(["JPY", "KRW"]);

export function money(value: string | number | null | undefined, currency = "JPY"): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  const digits = ZERO_DECIMAL.has(currency) ? 0 : 2;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n);
}

export function compactMoney(value: string | number, currency = "JPY"): string {
  const n = typeof value === "number" ? value : Number(value);
  return new Intl.NumberFormat("en-US", { style: "currency", currency, notation: "compact" }).format(n);
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(new Date(iso));
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - Date.now();
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const mins = Math.round(diff / 60000);
  if (Math.abs(mins) < 60) return rtf.format(mins, "minute");
  const hours = Math.round(mins / 60);
  if (Math.abs(hours) < 48) return rtf.format(hours, "hour");
  return rtf.format(Math.round(hours / 24), "day");
}

export function percent(ratio: string | number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined) return "—";
  return `${(Number(ratio) * 100).toFixed(digits)}%`;
}

export const CATEGORIES: Record<string, string> = {
  it_services: "IT services",
  travel: "Travel",
  facilities: "Facilities",
  marketing: "Marketing",
  professional_services: "Professional services",
  hardware: "Hardware",
  software: "Software",
  logistics: "Logistics",
  office_supplies: "Office supplies",
  manufacturing: "Manufacturing",
};

export const ROLE_LABELS: Record<string, string> = {
  employee: "Employee",
  manager: "Manager",
  department_head: "Department Head",
  procurement_officer: "Procurement Officer",
  finance_analyst: "Finance Analyst",
  finance_manager: "Finance Manager",
  vendor: "Vendor",
  org_admin: "Organization Admin",
  platform_admin: "Platform Admin",
  read_only_analyst: "Read-only Analyst",
};

export const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  pending_approval: "Pending approval",
  policy_blocked: "Blocked by policy",
  approved: "Approved",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

export const label = (map: Record<string, string>, key: string) => map[key] ?? key.replace(/_/g, " ");

/** Fiscal year labelled by its starting calendar year (April start: Apr 2026–Mar 2027 = FY2026). */
export function currentFiscalYear(startMonth: number, now: Date = new Date()): number {
  return now.getMonth() + 1 >= startMonth ? now.getFullYear() : now.getFullYear() - 1;
}

