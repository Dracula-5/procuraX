import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Button, ErrorNotice, Field, Input } from "../components/ui";
import { api, ApiError } from "../lib/api";
import type { TokenResponse } from "../lib/types";
import { AuthShell } from "./Login";

export function AcceptInvite() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");

  const accept = useMutation({
    mutationFn: () => api.post<TokenResponse>("/auth/accept-invitation", { token, full_name: fullName, password }),
    onSuccess: (res) => {
      signIn(res);
      navigate("/app", { replace: true });
    },
  });
  const fieldErrors = accept.error instanceof ApiError ? accept.error.fieldErrors() : {};
  const submit = (e: FormEvent) => {
    e.preventDefault();
    accept.mutate();
  };

  return (
    <AuthShell title="Join your organisation" subtitle="Set your name and password to activate the invitation.">
      {!token ? (
        <ErrorNotice error={new Error("This link is missing its invitation token.")} />
      ) : (
        <form onSubmit={submit} className="space-y-4 rounded-lg border border-border bg-surface p-5">
          <Field label="Your name" error={fieldErrors.full_name}>
            <Input required value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </Field>
          <Field label="Password" error={fieldErrors.password} hint="At least 10 characters, including a letter and a digit.">
            <Input required type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {!Object.keys(fieldErrors).length && <ErrorNotice error={accept.error} />}
          <Button type="submit" className="w-full" loading={accept.isPending}>
            Activate account
          </Button>
        </form>
      )}
    </AuthShell>
  );
}

