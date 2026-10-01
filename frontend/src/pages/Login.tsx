import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { RoleBadge } from "../components/badges";
import { DeploymentNotice } from "../components/DeploymentNotice";
import { useMeta } from "../lib/meta";
import { Button, ErrorNotice, Field, Input } from "../components/ui";
import { api } from "../lib/api";
import type { DemoPersona, TokenResponse } from "../lib/types";

export function AuthShell({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-start justify-center px-4 py-12 sm:items-center">
      <div className="w-full max-w-md">
        <Link to="/" className="mb-8 flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="size-7" />
          <span className="font-semibold tracking-tight">ProcuraX</span>
        </Link>
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
        <div className="mt-6">{children}</div>
      </div>
    </div>
  );
}

export function Login() {
  const { me, signIn } = useAuth();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/app";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const registration = useMeta().data?.registration_enabled !== false;

  const personas = useQuery({ queryKey: ["demo-personas"], queryFn: () => api.get<DemoPersona[]>("/auth/demo-personas") });
  const login = useMutation({
    mutationFn: () => api.post<TokenResponse>("/auth/login", { email, password }),
    onSuccess: (res) => {
      signIn(res);
      navigate(from, { replace: true });
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
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  return (
    <AuthShell title="Sign in" subtitle="Use your organisation account, or explore the demo tenant as any persona.">
      <div className="mb-4 overflow-hidden rounded-md"><DeploymentNotice /></div>
      <form onSubmit={submit} className="space-y-4 rounded-lg border border-border bg-surface p-5">
        <Field label="E-mail">
          <Input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password">
          <Input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorNotice error={login.error} />
        <Button type="submit" className="w-full" loading={login.isPending}>
          Sign in
        </Button>
        {registration && (
          <p className="text-center text-sm text-muted">
            New organisation?{" "}
            <Link className="font-medium text-primary hover:underline" to="/register">
              Create one
            </Link>
          </p>
        )}
      </form>

      {personas.data && personas.data.length > 0 && (
        <div className="mt-8">
          <h2 className="text-sm font-semibold">Demo tenant personas</h2>
          <p className="mt-1 text-xs text-muted">
            Fictional company and people. Sign in as each role to see how access and approvals differ.
          </p>
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
                    {p.roles.filter((r) => r !== "employee" || p.roles.length === 1).map((r) => (
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

