import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Button, ErrorNotice, Field, Input, Select } from "../components/ui";
import { api, ApiError } from "../lib/api";
import type { TokenResponse } from "../lib/types";
import { AuthShell } from "./Login";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function Register() {
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
    <AuthShell
      title="Create your organisation"
      subtitle="You become the organisation administrator. You can then invite requesters, approvers, procurement and finance."
    >
      <form onSubmit={submit} className="space-y-4 rounded-lg border border-border bg-surface p-5">
        <Field label="Organisation name" error={fieldErrors.organization_name}>
          <Input required value={form.organization_name} onChange={set("organization_name")} />
        </Field>
        <Field label="Your name" error={fieldErrors.full_name}>
          <Input required autoComplete="name" value={form.full_name} onChange={set("full_name")} />
        </Field>
        <Field label="Work e-mail" error={fieldErrors.email}>
          <Input required type="email" autoComplete="email" value={form.email} onChange={set("email")} />
        </Field>
        <Field label="Password" error={fieldErrors.password} hint="At least 10 characters, including a letter and a digit.">
          <Input required type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Currency">
            <Select value={form.base_currency} onChange={set("base_currency")}>
              {["JPY", "USD", "EUR", "GBP"].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </Select>
          </Field>
          <Field label="Fiscal year starts">
            <Select value={form.fiscal_year_start_month} onChange={set("fiscal_year_start_month")}>
              {MONTHS.map((m, i) => (
                <option key={m} value={i + 1}>
                  {m}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {!Object.keys(fieldErrors).length && <ErrorNotice error={register.error} />}
        <Button type="submit" className="w-full" loading={register.isPending}>
          Create organisation
        </Button>
        <p className="text-center text-sm text-muted">
          Already registered?{" "}
          <Link className="font-medium text-primary hover:underline" to="/login">
            Sign in
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}

