// Bank-account form state and its client-side Zengin checks (the API validates again on save).
import { useTranslation } from "react-i18next";
import i18n from "../i18n";
import { fieldLabel } from "./errors";
import { checkDigits, checkKanaField, NAME_LIMITS, type ZenginIssue } from "./zengin";

export interface BankAccountValue {
  bank_code: string;
  bank_name_kana: string;
  branch_code: string;
  branch_name_kana: string;
  account_type: string;
  account_number: string;
  holder_name_kana: string;
}

export const EMPTY_BANK_ACCOUNT: BankAccountValue = {
  bank_code: "",
  bank_name_kana: "",
  branch_code: "",
  branch_name_kana: "",
  account_type: "1",
  account_number: "",
  holder_name_kana: "",
};

type Issues = Partial<Record<string, ZenginIssue>>;

/** Client-side Zengin checks, mirroring the API (which validates again on save). */
export function bankAccountIssues(value: BankAccountValue): Issues {
  const issues: Issues = {};
  const digits = (key: keyof BankAccountValue, exact: number | null, max: number) => {
    const issue = checkDigits(value[key], exact, max);
    if (issue) issues[key] = issue;
  };
  digits("bank_code", 4, 4);
  digits("branch_code", 3, 3);
  digits("account_number", null, 7);
  for (const key of ["bank_name_kana", "branch_name_kana", "holder_name_kana"] as const) {
    const { issue } = checkKanaField(value[key], NAME_LIMITS[key]);
    if (issue) issues[key] = issue;
  }
  return issues;
}

export function useIssueText() {
  const { t } = useTranslation();
  return (issue: ZenginIssue | undefined): string | undefined => {
    if (!issue) return undefined;
    switch (issue.kind) {
      case "required":
        return t("bank.issue.required");
      case "digits":
        return t("bank.issue.digits", { length: issue.length });
      case "too_long":
        return t("bank.issue.tooLong", { max: issue.max, length: issue.length });
      case "invalid_chars":
        return t("bank.issue.invalidChars", { chars: issue.chars });
    }
  };
}

/** Field-level errors from an API refusal (details.field), translated; other errors go to ErrorNotice. */
export function serverFieldErrors(error: unknown): Partial<Record<string, string>> {
  const details = (error as { details?: { reason?: string; field?: string } } | null)?.details;
  if (!details?.field || !details.reason?.startsWith("zengin_")) return {};
  const key = `errors.reasons.${details.reason}`;
  if (!i18n.exists(key)) return {};
  return { [details.field]: String(i18n.t(key as never, { field: fieldLabel(details.field) } as never)) };
}
