import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { date, dateTime, number } from "../lib/format";
import type { ExchangeRate, RatesResponse } from "../lib/types";

const today = () => new Date().toISOString().slice(0, 10);

/** Exchange rates for requests in another currency: manual entry, optional ECB reference rates. */
export function ExchangeRates() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const canEdit = can("budget:manage") || can("organization:manage");
  const qc = useQueryClient();
  const rates = useQuery({ queryKey: ["fx-rates"], queryFn: () => api.get<RatesResponse>("/fx/rates") });
  const [form, setForm] = useState({ currency: "", rate: "", valid_on: today() });
  const save = useMutation({
    mutationFn: () => api.put<ExchangeRate>("/fx/rates", { ...form, currency: form.currency || rates.data?.supported_currencies[0] }),
    onSuccess: () => {
      setForm((f) => ({ ...f, rate: "" }));
      void qc.invalidateQueries({ queryKey: ["fx-rates"] });
    },
  });
  const refresh = useMutation({
    mutationFn: () => api.post<{ reference_date: string; updated: string[] }>("/fx/rates/refresh"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["fx-rates"] }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  if (rates.isLoading) return <Spinner />;
  const data = rates.data;
  const base = data?.base_currency ?? "JPY";
  const source = (s: string) => t(`fx.sources.${s === "ecb" ? "ecb" : "manual"}`);
  const rateTable = (rows: ExchangeRate[], withRecorded: boolean) => (
    <Table>
      <thead>
        <tr>
          <Th>{t("fx.currency")}</Th>
          <Th className="text-right">{t("fx.rate")}</Th>
          <Th>{t("fx.validOn")}</Th>
          <Th>{t("fx.source")}</Th>
          {withRecorded && <Th>{t("fx.recordedAt")}</Th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <Td className="font-medium">{r.currency}</Td>
            <Td className="tabular text-right">
              <span title={t("fx.perUnit", { currency: r.currency, rate: number(r.rate, 8), base })}>{number(r.rate, 8)}</span>
            </Td>
            <Td>{date(r.valid_on)}</Td>
            <Td>{source(r.source)}</Td>
            {withRecorded && <Td>{dateTime(r.created_at)}</Td>}
          </tr>
        ))}
      </tbody>
    </Table>
  );

  return (
    <>
      <PageHeader
        title={t("fx.title")}
        subtitle={t("fx.subtitle", { base })}
        actions={
          canEdit && data?.ecb_enabled ? (
            <Button variant="secondary" loading={refresh.isPending} onClick={() => refresh.mutate()}>
              <RefreshCw className="size-4" /> {t("fx.refresh")}
            </Button>
          ) : undefined
        }
      />
      <ErrorNotice error={rates.error ?? refresh.error} />
      {refresh.isSuccess && (
        <p className="mb-4 text-sm text-ok">
          {t("fx.refreshed", { date: date(refresh.data.reference_date), currencies: refresh.data.updated.join(", ") })}
        </p>
      )}
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          <Card title={t("fx.latestTitle")}>
            {data?.latest.length ? rateTable(data.latest, false) : <EmptyState title={t("fx.empty")}>{t("fx.emptyBody")}</EmptyState>}
            <p className="mt-3 text-xs text-muted">{t("fx.manualWins")}</p>
          </Card>
          {!!data?.history.length && <Card title={t("fx.history")}>{rateTable(data.history, true)}</Card>}
        </div>
        <Card title={t("fx.addTitle")}>
          {canEdit ? (
            <form onSubmit={submit} className="space-y-3">
              <Field label={t("fx.currency")}>
                <Select value={form.currency || data?.supported_currencies[0] || ""} onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value }))}>
                  {data?.supported_currencies.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={t("fx.rate")} hint={t("fx.rateHint", { base })}>
                <Input type="number" required min="0" step="any" value={form.rate} onChange={(e) => setForm((f) => ({ ...f, rate: e.target.value }))} />
              </Field>
              <Field label={t("fx.validOn")}>
                <Input type="date" required max={today()} value={form.valid_on} onChange={(e) => setForm((f) => ({ ...f, valid_on: e.target.value }))} />
              </Field>
              <ErrorNotice error={save.error} />
              {save.isSuccess && <p className="text-sm text-ok">{t("fx.saved")}</p>}
              <Button type="submit" loading={save.isPending}>
                {t("fx.save")}
              </Button>
            </form>
          ) : (
            <p className="text-sm text-muted">{t("fx.readOnly")}</p>
          )}
          <p className="mt-4 text-xs text-muted">{data?.ecb_enabled ? t("fx.ecbNote") : t("fx.refreshDisabled")}</p>
        </Card>
      </div>
    </>
  );
}
