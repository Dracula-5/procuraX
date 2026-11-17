import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { RoleBadge } from "../components/badges";
import { DeploymentNotice } from "../components/DeploymentNotice";
import { LanguageSwitcher } from "../components/LanguageSwitcher";
import { Button, ErrorNotice, Field, Input } from "../components/ui";
import { api, apiUrl } from "../lib/api";
import { useMeta } from "../lib/meta";
import type { AuthConfig, DemoPersona, MfaChallenge, TokenResponse } from "../lib/types";

export function AuthShell({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-start justify-center px-4 py-12 sm:items-center">
      <div className="w-full max-w-md">
        <div className="mb-8 flex items-center justify-between gap-3">
          <Link to="/" className="flex items-center gap-2">
            <img src="/favicon.svg" alt="" className="size-7" />
            <span className="font-semibold tracking-tight">ProcuraX</span>
          </Link>
          <LanguageSwitcher />
        </div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
        <div className="mt-6">{children}</div>
      </div>
    </div>
  );
}

/** Second sign-in step: a code from the authenticator app, or one recovery code. */
function MfaStep({ challenge, onSignedIn, onBack }: { challenge: MfaChallenge; onSignedIn: (res: TokenResponse) => void; onBack: () => void }) {
  const { t } = useTranslation();
  const [recovery, setRecovery] = useState(false);
  const [value, setValue] = useState("");
  const verify = useMutation({
    mutationFn: () =>
      api.post<TokenResponse>("/auth/mfa/verify", { mfa_token: challenge.mfa_token, ...(recovery ? { recovery_code: value } : { code: value }) }),
    onSuccess: onSignedIn,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    verify.mutate();
  };
  return (
    <AuthShell title={t("security.login.mfaTitle")} subtitle={t("security.login.mfaSubtitle")}>
      <form onSubmit={submit} className="space-y-4 rounded-lg border border-border bg-surface p-5">
        <Field label={recovery ? t("security.mfa.recoveryCode") : t("security.mfa.code")}>
          <Input
            autoFocus
            required
            autoComplete="one-time-code"
            inputMode={recovery ? "text" : "numeric"}
            maxLength={recovery ? 20 : 6}
            value={value}
            onChange={(e) => setValue(e.target.value.trim())}
          />
        </Field>
        <button type="button" className="text-sm text-primary hover:underline" onClick={() => { setRecovery((r) => !r); setValue(""); }}>
          {recovery ? t("security.login.useApp") : t("security.login.useRecovery")}
        </button>
        <ErrorNotice error={verify.error} />
        <Button type="submit" className="w-full" loading={verify.isPending}>
          {t("security.login.verify")}
        </Button>
        <button type="button" className="block w-full text-center text-sm text-muted hover:underline" onClick={onBack}>
          {t("security.login.back")}
        </button>
      </form>
    </AuthShell>
  );
}

export function Login() {
  const { t, i18n } = useTranslation();
  const { me, signIn } = useAuth();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/app";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<MfaChallenge | null>(null);
  const registration = useMeta().data?.registration_enabled !== false;
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api.get<AuthConfig>("/auth/config") });
  const ssoError = useSearchParams()[0].get("sso_error");
  const ssoMessage = ssoError
    ? i18n.exists(`security.ssoErrors.${ssoError}`)
      ? String(t(`security.ssoErrors.${ssoError}` as never))
      : t("security.ssoErrors.generic")
    : null;

  const personas = useQuery({ queryKey: ["demo-personas"], queryFn: () => api.get<DemoPersona[]>("/auth/demo-personas") });
  const finish = (res: TokenResponse) => {
    signIn(res);
    navigate(from, { replace: true });
  };
  const login = useMutation({
    mutationFn: () => api.post<TokenResponse | MfaChallenge>("/auth/login", { email, password }),
    onSuccess: (res) => {
      if ("mfa_required" in res) setChallenge(res);
      else finish(res);
    },
  });
  const demo = useMutation({
    mutationFn: (personaEmail: string) => api.post<TokenResponse>("/auth/demo-login", { email: personaEmail }),
    onSuccess: (res) => {
      signIn(res);
      navigate("/app", { replace: true });
    },
  });

  if (me) return <Navigate to="/app" replace />;
  if (challenge) return <MfaStep challenge={challenge} onSignedIn={finish} onBack={() => setChallenge(null)} />;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  return (
    <AuthShell title={t("auth.signIn")} subtitle={t("auth.signInSubtitle")}>
      <div className="mb-4 overflow-hidden rounded-md">
        <DeploymentNotice />
      </div>
      {ssoMessage && (
        <p role="alert" className="mb-4 rounded-md border border-danger/40 bg-danger-soft px-3 py-2 text-sm">
          {ssoMessage}
        </p>
      )}
      {config.data?.oidc_enabled && (
        <div className="mb-4 space-y-3">
          <Button type="button" variant="secondary" className="w-full" onClick={() => window.location.assign(apiUrl("/auth/oidc/start"))}>
            {t("security.login.sso", { name: config.data.oidc_display_name ?? "SSO" })}
          </Button>
          <p className="text-center text-xs text-muted">{t("security.login.or")}</p>
        </div>
      )}
      <form onSubmit={submit} className="space-y-4 rounded-lg border border-border bg-surface p-5">
        <Field label={t("auth.email")}>
          <Input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label={t("auth.password")}>
          <Input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorNotice error={login.error} />
        <Button type="submit" className="w-full" loading={login.isPending}>
          {t("auth.signIn")}
        </Button>
        {registration && (
          <p className="text-center text-sm text-muted">
            {t("auth.newOrg")}{" "}
            <Link className="font-medium text-primary hover:underline" to="/register">
              {t("auth.createOne")}
            </Link>
          </p>
        )}
      </form>

      {personas.data && personas.data.length > 0 && (
        <div className="mt-8">
          <h2 className="text-sm font-semibold">{t("auth.personasTitle")}</h2>
          <p className="mt-1 text-xs text-muted">{t("auth.personasBody")}</p>
          <ErrorNotice error={demo.error} />
          <ul className="mt-3 divide-y divide-border rounded-lg border border-border bg-surface">
            {personas.data.map((p) => (
              <li key={p.email}>
                <button
                  onClick={() => demo.mutate(p.email)}
                  disabled={demo.isPending}
                  className="flex w-full items-start justify-between gap-3 px-4 py-3 text-left hover:bg-surface-2 disabled:opacity-60"
                >
                  <span>
                    <span className="block text-sm font-medium">{p.full_name}</span>
                    <span className="block text-xs text-muted">{p.job_title}</span>
                  </span>
                  <span className="flex flex-wrap justify-end gap-1">
                    {p.roles
                      .filter((r) => r !== "employee" || p.roles.length === 1)
                      .map((r) => (
                        <RoleBadge key={r} role={r} />
                      ))}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </AuthShell>
  );
}
