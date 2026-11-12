import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Button, ErrorNotice, Field, Input, Select } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { monthNames } from "../lib/format";
import type { TokenResponse } from "../lib/types";
import { AuthShell } from "./Login";

export function Register() {
  const { t } = useTranslation();
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({
    organization_name: "",
    full_name: "",
    email: "",
    password: "",
    base_currency: "JPY",
    fiscal_year_start_month: 4,
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: k === "fiscal_year_start_month" ? Number(e.target.value) : e.target.value }));

  const register = useMutation({
    mutationFn: () => api.post<TokenResponse>("/auth/register", form),
    onSuccess: (res) => {
      signIn(res);
      navigate("/app/admin/structure", { replace: true });
    },
  });
  const fieldErrors = register.error instanceof ApiError ? register.error.fieldErrors() : {};

  const submit = (e: FormEvent) => {
    e.preventDefault();
    register.mutate();
  };

  return (
    <AuthShell title={t("auth.registerTitle")} subtitle={t("auth.registerSubtitle")}>
      <form onSubmit={submit} className="space-y-4 rounded-lg border border-border bg-surface p-5">
        <Field label={t("auth.orgName")} error={fieldErrors.organization_name}>
          <Input required value={form.organization_name} onChange={set("organization_name")} />
        </Field>
        <Field label={t("auth.yourName")} error={fieldErrors.full_name}>
          <Input required autoComplete="name" value={form.full_name} onChange={set("full_name")} />
        </Field>
        <Field label={t("auth.workEmail")} error={fieldErrors.email}>
          <Input required type="email" autoComplete="email" value={form.email} onChange={set("email")} />
        </Field>
        <Field label={t("auth.password")} error={fieldErrors.password} hint={t("auth.passwordHint")}>
          <Input required type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("auth.currency")}>
            <Select value={form.base_currency} onChange={set("base_currency")}>
              {["JPY", "USD", "EUR", "GBP"].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </Select>
          </Field>
          <Field label={t("auth.fyStarts")}>
            <Select value={form.fiscal_year_start_month} onChange={set("fiscal_year_start_month")}>
              {monthNames().map((m, i) => (
                <option key={m} value={i + 1}>
                  {m}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {!Object.keys(fieldErrors).length && <ErrorNotice error={register.error} />}
        <Button type="submit" className="w-full" loading={register.isPending}>
          {t("auth.createOrg")}
        </Button>
        <p className="text-center text-sm text-muted">
          {t("auth.alreadyRegistered")}{" "}
          <Link className="font-medium text-primary hover:underline" to="/login">
            {t("auth.signIn")}
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}
