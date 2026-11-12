import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { BankAccountFields } from "../components/BankAccountFields";
import { bankAccountIssues, EMPTY_BANK_ACCOUNT, serverFieldErrors, type BankAccountValue } from "../lib/bankAccount";
import { Button, Card, ErrorNotice, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import type { Vendor } from "../lib/types";

type SavedAccount = BankAccountValue | null;

export function SavedAccountSummary({ account }: { account: SavedAccount }) {
  const { t } = useTranslation();
  if (!account) return <p className="text-sm text-muted">{t("bank.noneSaved")}</p>;
  const rows: [string, string][] = [
    [t("bank.bankCode"), `${account.bank_code} ${account.bank_name_kana}`],
    [t("bank.branchCode"), `${account.branch_code} ${account.branch_name_kana}`],
    [t("bank.accountType"), t(`bank.accountTypes.${account.account_type as "1" | "2" | "4"}`)],
    [t("bank.accountNumber"), account.account_number],
    [t("bank.holderName"), account.holder_name_kana],
  ];
  return (
    <dl className="space-y-1.5 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-4">
          <dt className="text-muted">{k}</dt>
          <dd className="font-mono" lang="ja">
            {v}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function VendorBankAccount() {
  const { t } = useTranslation();
  const { id } = useParams();
  const qc = useQueryClient();
  const vendor = useQuery({ queryKey: ["vendor", id], queryFn: () => api.get<Vendor>(`/vendors/${id}`) });
  const saved = useQuery({
    queryKey: ["vendor-bank-account", id],
    queryFn: () => api.get<{ vendor_id: string; bank_account: SavedAccount }>(`/vendors/${id}/bank-account`),
  });
  const [form, setForm] = useState<BankAccountValue>(EMPTY_BANK_ACCOUNT);
  const save = useMutation({
    mutationFn: () => api.put<{ bank_account: SavedAccount }>(`/vendors/${id}/bank-account`, form),
    onSuccess: (data) => {
      qc.setQueryData(["vendor-bank-account", id], { vendor_id: id, bank_account: data.bank_account });
      setForm(EMPTY_BANK_ACCOUNT); // the account number is masked once saved, so the form never holds it again
    },
  });
  const issues = bankAccountIssues(form);
  const serverErrors = serverFieldErrors(save.error);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  if (vendor.isLoading || saved.isLoading) return <Spinner />;
  if (!vendor.data) return <ErrorNotice error={vendor.error} />;

  return (
    <>
      <Link to="/app/vendors" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" /> {t("bank.backToVendors")}
      </Link>
      <PageHeader title={t("bank.vendorTitle", { name: vendor.data.name })} subtitle={t("bank.vendorSubtitle")} />
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card title={t("bank.enterTitle")}>
          <form onSubmit={submit} className="space-y-4">
            <BankAccountFields value={form} onChange={setForm} serverErrors={serverErrors} />
            {!Object.keys(serverErrors).length && <ErrorNotice error={save.error} />}
            {save.isSuccess && <p className="text-sm text-ok">{t("bank.saved")}</p>}
            <Button type="submit" loading={save.isPending} disabled={Object.keys(issues).length > 0}>
              {t("bank.save")}
            </Button>
          </form>
        </Card>
        <Card title={t("bank.currentTitle")}>
          <SavedAccountSummary account={saved.data?.bank_account ?? null} />
          <p className="mt-3 text-xs text-muted">{t("bank.maskedNote")}</p>
        </Card>
      </div>
    </>
  );
}
