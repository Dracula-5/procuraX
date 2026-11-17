import { useTranslation } from "react-i18next";
import { bankAccountIssues, useIssueText, type BankAccountValue } from "../lib/bankAccount";
import { checkKanaField, NAME_LIMITS } from "../lib/zengin";
import { Field, Input, Select } from "./ui";

type KanaKey = keyof typeof NAME_LIMITS;

/** A name field that shows how the bank will receive it (half-width kana) and any problem, while typing. */
export function KanaField({
  name,
  label,
  value,
  onChange,
  serverError,
}: {
  name: KanaKey;
  label: string;
  value: string;
  onChange: (value: string) => void;
  serverError?: string;
}) {
  const { t } = useTranslation();
  const issueText = useIssueText();
  const { preview, issue } = checkKanaField(value, NAME_LIMITS[name]);
  const showIssue = value !== "" && issue && issue.kind !== "required";
  return (
    <Field
      label={label}
      error={serverError ?? (showIssue ? issueText(issue) : undefined)}
      hint={preview ? t("bank.preview", { value: preview, length: [...preview].length, max: NAME_LIMITS[name] }) : t("bank.kanaHint")}
    >
      <Input name={name} value={value} onChange={(e) => onChange(e.target.value)} aria-invalid={Boolean(showIssue || serverError)} lang="ja" />
    </Field>
  );
}

export function BankAccountFields({
  value,
  onChange,
  serverErrors,
}: {
  value: BankAccountValue;
  onChange: (value: BankAccountValue) => void;
  serverErrors: Partial<Record<string, string>>;
}) {
  const { t } = useTranslation();
  const issueText = useIssueText();
  const issues = bankAccountIssues(value);
  const set = (key: keyof BankAccountValue) => (next: string) => onChange({ ...value, [key]: next });
  const digitError = (key: keyof BankAccountValue) =>
    serverErrors[key] ?? (value[key] !== "" && issues[key]?.kind !== "required" ? issueText(issues[key]) : undefined);
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label={t("bank.bankCode")} error={digitError("bank_code")} hint={t("bank.bankCodeHint")}>
        <Input name="bank_code" inputMode="numeric" maxLength={4} value={value.bank_code} onChange={(e) => set("bank_code")(e.target.value.trim())} />
      </Field>
      <KanaField name="bank_name_kana" label={t("bank.bankName")} value={value.bank_name_kana} onChange={set("bank_name_kana")} serverError={serverErrors.bank_name_kana} />
      <Field label={t("bank.branchCode")} error={digitError("branch_code")} hint={t("bank.branchCodeHint")}>
        <Input name="branch_code" inputMode="numeric" maxLength={3} value={value.branch_code} onChange={(e) => set("branch_code")(e.target.value.trim())} />
      </Field>
      <KanaField name="branch_name_kana" label={t("bank.branchName")} value={value.branch_name_kana} onChange={set("branch_name_kana")} serverError={serverErrors.branch_name_kana} />
      <Field label={t("bank.accountType")} error={serverErrors.account_type}>
        <Select name="account_type" value={value.account_type} onChange={(e) => set("account_type")(e.target.value)}>
          {(["1", "2", "4"] as const).map((type) => (
            <option key={type} value={type}>
              {t(`bank.accountTypes.${type}`)}
            </option>
          ))}
        </Select>
      </Field>
      <Field label={t("bank.accountNumber")} error={digitError("account_number")} hint={t("bank.accountNumberHint")}>
        <Input name="account_number" inputMode="numeric" maxLength={7} value={value.account_number} onChange={(e) => set("account_number")(e.target.value.trim())} />
      </Field>
      <div className="sm:col-span-2">
        <KanaField name="holder_name_kana" label={t("bank.holderName")} value={value.holder_name_kana} onChange={set("holder_name_kana")} serverError={serverErrors.holder_name_kana} />
      </div>
    </div>
  );
}
