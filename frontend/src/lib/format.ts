// Number, money and date formatting in the active UI language. Components re-render on a language
// change because they subscribe through useTranslation(); these helpers read the current language.
import i18n from "../i18n";

const ZERO_DECIMAL = new Set(["JPY", "KRW"]);

/** BCP 47 tags per UI language: English keeps the original US numbers / UK dates; Japanese uses ja-JP. */
export function numberLocale(lang: string = i18n.language): string {
  return lang?.startsWith("ja") ? "ja-JP" : "en-US";
}
export function dateLocale(lang: string = i18n.language): string {
  return lang?.startsWith("ja") ? "ja-JP" : "en-GB";
}

export function money(value: string | number | null | undefined, currency = "JPY"): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  const digits = ZERO_DECIMAL.has(currency) ? 0 : 2;
  return new Intl.NumberFormat(numberLocale(), {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n);
}

export function compactMoney(value: string | number, currency = "JPY"): string {
  const n = typeof value === "number" ? value : Number(value);
  return new Intl.NumberFormat(numberLocale(), { style: "currency", currency, notation: "compact" }).format(n);
}

export function number(value: string | number | null | undefined, maximumFractionDigits = 2): string {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat(numberLocale(), { maximumFractionDigits }).format(Number(value));
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(dateLocale(), { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(dateLocale(), { dateStyle: "medium" }).format(new Date(iso));
}

/** "Apr 2026" / "2026年4月" for a month bucket such as 2026-04-01. */
export function monthLabel(iso: string): string {
  return new Intl.DateTimeFormat(dateLocale(), { month: "short", year: "numeric" }).format(new Date(iso));
}

/** Month names for selects (index 0 = January) in the active language. */
export function monthNames(): string[] {
  const fmt = new Intl.DateTimeFormat(dateLocale(), { month: "long" });
  return Array.from({ length: 12 }, (_, i) => fmt.format(new Date(2000, i, 1)));
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - Date.now();
  const rtf = new Intl.RelativeTimeFormat(i18n.language?.startsWith("ja") ? "ja" : "en", { numeric: "auto" });
  const mins = Math.round(diff / 60000);
  if (Math.abs(mins) < 60) return rtf.format(mins, "minute");
  const hours = Math.round(mins / 60);
  if (Math.abs(hours) < 48) return rtf.format(hours, "hour");
  return rtf.format(Math.round(hours / 24), "day");
}

export function percent(ratio: string | number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined) return "—";
  return new Intl.NumberFormat(numberLocale(), {
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(ratio));
}

/** Fiscal year labelled by its starting calendar year (April start: Apr 2026–Mar 2027 = FY2026). */
export function currentFiscalYear(startMonth: number, now: Date = new Date()): number {
  return now.getMonth() + 1 >= startMonth ? now.getFullYear() : now.getFullYear() - 1;
}
