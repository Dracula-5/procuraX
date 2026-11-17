import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { BankAccountFields, KanaField } from "../components/BankAccountFields";
import { Button, Card, ErrorNotice, Field, Input, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { bankAccountIssues, EMPTY_BANK_ACCOUNT, serverFieldErrors, useIssueText, type BankAccountValue } from "../lib/bankAccount";
import { checkDigits, checkKanaField, NAME_LIMITS } from "../lib/zengin";
import { SavedAccountSummary } from "./VendorBankAccount";

type PayerSettings = BankAccountValue & { consignor_code: string; consignor_name_kana: string };

const EMPTY: PayerSettings = { ...EMPTY_BANK_ACCOUNT, consignor_code: "", consignor_name_kana: "" };

/** Payer (振込依頼人) details for the Zengin transfer file: maintained by the org admin, used by finance. */
export function PaymentSettings() {
  const { t } = useTranslation();
  const issueText = useIssueText();
  const { can } = useAuth();
  const canEdit = can("organization:manage");
  const qc = useQueryClient();
  const saved = useQuery({
    queryKey: ["payment-settings"],
    queryFn: () => api.get<{ payment_settings: PayerSettings | null }>("/organizations/current/payment-settings"),
  });
  const [form, setForm] = useState<PayerSettings>(EMPTY);
  const save = useMutation({
    mutationFn: () => api.put<{ payment_settings: PayerSettings }>("/organizations/current/payment-settings", form),
    onSuccess: (data) => {
      qc.setQueryData(["payment-settings"], data);
      setForm(EMPTY);
    },
  });

  const issues = {
    ...bankAccountIssues(form),
    ...(checkDigits(form.consignor_code, 10, 10) ? { consignor_code: checkDigits(form.consignor_code, 10, 10)! } : {}),
    ...(checkKanaField(form.consignor_name_kana, NAME_LIMITS.consignor_name_kana).issue
      ? { consignor_name_kana: checkKanaField(form.consignor_name_kana, NAME_LIMITS.consignor_name_kana).issue! }
      : {}),
  };
  const serverErrors = serverFieldErrors(save.error);
  const consignorCodeError =
    serverErrors.consignor_code ?? (form.consignor_code !== "" && issues.consignor_code?.kind !== "required" ? issueText(issues.consignor_code) : undefined);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  if (saved.isLoading) return <Spinner />;
  const current = saved.data?.payment_settings ?? null;

  return (
    <>
      <PageHeader title={t("paymentSettings.title")} subtitle={t("paymentSettings.subtitle")} />
      <ErrorNotice error={saved.error} />
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        {canEdit ? (
          <Card title={t("paymentSettings.editTitle")}>
            <form onSubmit={submit} className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label={t("paymentSettings.consignorCode")} error={consignorCodeError} hint={t("paymentSettings.consignorCodeHint")}>
                  <Input name="consignor_code" inputMode="numeric" maxLength={10} value={form.consignor_code} onChange={(e) => setForm((f) => ({ ...f, consignor_code: e.target.value.trim() }))} />
                </Field>
                <KanaField
                  name="consignor_name_kana"
                  label={t("paymentSettings.consignorName")}
                  value={form.consignor_name_kana}
                  onChange={(value) => setForm((f) => ({ ...f, consignor_name_kana: value }))}
                  serverError={serverErrors.consignor_name_kana}
                />
              </div>
              <p className="text-sm font-medium">{t("paymentSettings.debitAccount")}</p>
              <BankAccountFields value={form} onChange={(value) => setForm((f) => ({ ...f, ...value }))} serverErrors={serverErrors} />
              {!Object.keys(serverErrors).length && <ErrorNotice error={save.error} />}
              {save.isSuccess && <p className="text-sm text-ok">{t("bank.saved")}</p>}
              <Button type="submit" loading={save.isPending} disabled={Object.keys(issues).length > 0}>
                {t("bank.save")}
              </Button>
            </form>
          </Card>
        ) : (
          <Card title={t("paymentSettings.readOnlyTitle")}>
            <p className="text-sm text-muted">{t("paymentSettings.readOnlyBody")}</p>
          </Card>
        )}
        <Card title={t("bank.currentTitle")}>
          {current && (
            <dl className="mb-3 space-y-1.5 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-muted">{t("paymentSettings.consignorCode")}</dt>
                <dd className="font-mono">{current.consignor_code}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted">{t("paymentSettings.consignorName")}</dt>
                <dd className="font-mono" lang="ja">
                  {current.consignor_name_kana}
                </dd>
              </div>
            </dl>
          )}
          <SavedAccountSummary account={current} />
          <p className="mt-3 text-xs text-muted">{t("bank.maskedNote")}</p>
        </Card>
      </div>
    </>
  );
}
