import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, LogOut, ShieldCheck } from "lucide-react";
import qrcode from "qrcode-generator";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { Badge, Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { saveBlob } from "../lib/files";
import { date, relative } from "../lib/format";
import type { AuthConfig, Me, SessionInfo } from "../lib/types";

type Setup = { secret: string; otpauth_uri: string };

function QrCode({ text, alt }: { text: string; alt: string }) {
  const src = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    return qr.createDataURL(5, 4);
  }, [text]);
  return <img src={src} alt={alt} className="size-48 rounded border border-border bg-white p-1" />;
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <p className="text-sm font-medium">{t("security.mfa.recoveryTitle")}</p>
      <p className="text-sm text-muted">{t("security.mfa.recoveryBody")}</p>
      <ul className="grid grid-cols-2 gap-1 rounded-md border border-border bg-surface-2 p-3 font-mono text-sm" aria-label={t("security.mfa.recoveryTitle")}>
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" onClick={() => saveBlob(new Blob([codes.join("\n") + "\n"], { type: "text/plain" }), "procurax-recovery-codes.txt")}>
          <Download className="size-4" /> {t("security.mfa.downloadCodes")}
        </Button>
        <Button size="sm" onClick={onDone}>
          {t("security.mfa.done")}
        </Button>
      </div>
    </div>
  );
}

/** A code from the app, or (where allowed) a recovery code, then `onSubmit`. */
function CodeForm({ prompt, allowRecovery, pending, error, onSubmit, onCancel }: {
  prompt: string;
  allowRecovery: boolean;
  pending: boolean;
  error: unknown;
  onSubmit: (body: { code?: string; recovery_code?: string }) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [recovery, setRecovery] = useState(false);
  const [value, setValue] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(recovery ? { recovery_code: value } : { code: value });
  };
  return (
    <form onSubmit={submit} className="space-y-3">
      <p className="text-sm text-muted">{prompt}</p>
      <Field label={recovery ? t("security.mfa.recoveryCode") : t("security.mfa.code")}>
        <Input
          required
          autoComplete="one-time-code"
          inputMode={recovery ? "text" : "numeric"}
          maxLength={recovery ? 20 : 6}
          value={value}
          onChange={(e) => setValue(e.target.value.trim())}
        />
      </Field>
      {allowRecovery && (
        <button type="button" className="text-sm text-primary hover:underline" onClick={() => setRecovery((r) => !r)}>
          {recovery ? t("security.login.useApp") : t("security.login.useRecovery")}
        </button>
      )}
      <ErrorNotice error={error} />
      <div className="flex gap-2">
        <Button type="submit" size="sm" loading={pending}>
          {t("security.mfa.submit")}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {t("security.mfa.cancel")}
        </Button>
      </div>
    </form>
  );
}

function TwoFactor({ me }: { me: Me }) {
  const { t } = useTranslation();
  const { updateMe } = useAuth();
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api.get<AuthConfig>("/auth/config") });
  const [setup, setSetup] = useState<Setup | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [confirming, setConfirming] = useState<"off" | "codes" | null>(null);
  const reloadMe = async () => updateMe(await api.get<Me>("/auth/me"));

  const start = useMutation({ mutationFn: () => api.post<Setup>("/auth/mfa/setup"), onSuccess: setSetup });
  const enable = useMutation({
    mutationFn: () => api.post<{ recovery_codes: string[] }>("/auth/mfa/enable", { code }),
    onSuccess: (res) => {
      setSetup(null);
      setCode("");
      setCodes(res.recovery_codes);
      void reloadMe();
    },
  });
  const disable = useMutation({
    mutationFn: (body: { code?: string; recovery_code?: string }) => api.post("/auth/mfa/disable", body),
    onSuccess: () => {
      setConfirming(null);
      void reloadMe();
    },
  });
  const regenerate = useMutation({
    mutationFn: (body: { code?: string }) => api.post<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", body),
    onSuccess: (res) => {
      setConfirming(null);
      setCodes(res.recovery_codes);
    },
  });

  const enabled = Boolean(me.mfa_enabled);
  return (
    <Card title={t("security.mfa.title")}>
      <div className="mb-3 flex items-center gap-2">
        <ShieldCheck className="size-5 text-muted" />
        <Badge tone={enabled ? "ok" : "neutral"}>{enabled ? t("security.mfa.on") : t("security.mfa.off")}</Badge>
      </div>
      <p className="mb-4 text-sm text-muted">{t("security.mfa.intro")}</p>
      {codes ? (
        <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />
      ) : enabled ? (
        confirming ? (
          <CodeForm
            prompt={confirming === "off" ? t("security.mfa.turnOffConfirm") : t("security.mfa.newCodesConfirm")}
            allowRecovery={confirming === "off"}
            pending={disable.isPending || regenerate.isPending}
            error={confirming === "off" ? disable.error : regenerate.error}
            onSubmit={(body) => (confirming === "off" ? disable.mutate(body) : regenerate.mutate(body))}
            onCancel={() => setConfirming(null)}
          />
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={() => setConfirming("codes")}>
              {t("security.mfa.newCodes")}
            </Button>
            <Button size="sm" variant="danger" onClick={() => setConfirming("off")}>
              {t("security.mfa.turnOff")}
            </Button>
          </div>
        )
      ) : setup ? (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            enable.mutate();
          }}
        >
          <p className="text-sm">{t("security.mfa.scan")}</p>
          <QrCode text={setup.otpauth_uri} alt={t("security.mfa.qrAlt")} />
          <p className="text-xs text-muted">{t("security.mfa.manualKey")}</p>
          <p className="break-all font-mono text-sm" aria-label={t("security.mfa.manualKey")}>
            {setup.secret.replace(/(.{4})/g, "$1 ").trim()}
          </p>
          <Field label={t("security.mfa.code")}>
            <Input required autoComplete="one-time-code" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.trim())} />
          </Field>
          <ErrorNotice error={enable.error} />
          <div className="flex gap-2">
            <Button type="submit" size="sm" loading={enable.isPending}>
              {t("security.mfa.confirm")}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setSetup(null)}>
              {t("security.mfa.cancel")}
            </Button>
          </div>
        </form>
      ) : config.data && !config.data.mfa_available ? (
        <p className="text-sm text-muted">{t("security.mfa.unavailable")}</p>
      ) : (
        <>
          <ErrorNotice error={start.error} />
          <Button size="sm" loading={start.isPending} onClick={() => start.mutate()}>
            {t("security.mfa.start")}
          </Button>
        </>
      )}
    </Card>
  );
}

function device(agent: string | null, fallback: string): string {
  if (!agent) return fallback;
  const browser = /Edg\//.test(agent) ? "Edge" : /Chrome\//.test(agent) ? "Chrome" : /Firefox\//.test(agent) ? "Firefox" : /Safari\//.test(agent) ? "Safari" : null;
  const os = /iPhone|iPad/.test(agent) ? "iOS" : /Android/.test(agent) ? "Android" : /Windows/.test(agent) ? "Windows" : /Mac OS X/.test(agent) ? "macOS" : /Linux/.test(agent) ? "Linux" : null;
  return [browser, os].filter(Boolean).join(" · ") || agent.slice(0, 60);
}

function Sessions() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ["auth-sessions"], queryFn: () => api.get<SessionInfo[]>("/auth/sessions") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["auth-sessions"] });
  const revoke = useMutation({ mutationFn: (id: string) => api.del(`/auth/sessions/${id}`), onSuccess: refresh });
  const others = useMutation({ mutationFn: () => api.post<{ revoked: number }>("/auth/sessions/revoke-others"), onSuccess: refresh });
  const method = (m: string) => (i18n.exists(`security.sessions.methods.${m}`) ? String(t(`security.sessions.methods.${m}` as never)) : m);

  return (
    <Card
      title={t("security.sessions.title")}
      actions={
        <Button size="sm" variant="secondary" loading={others.isPending} onClick={() => others.mutate()} disabled={!sessions.data?.some((s) => !s.current)}>
          <LogOut className="size-4" /> {t("security.sessions.signOutOthers")}
        </Button>
      }
    >
      <p className="mb-3 text-sm text-muted">{t("security.sessions.intro")}</p>
      <ErrorNotice error={sessions.error ?? revoke.error ?? others.error} />
      {others.isSuccess && <p className="mb-2 text-sm text-ok">{t("security.sessions.signedOutOthers", { count: others.data.revoked })}</p>}
      {sessions.isLoading ? (
        <Spinner />
      ) : !sessions.data?.length ? (
        <EmptyState title={t("security.sessions.title")} />
      ) : (
        <ul className="divide-y divide-border">
          {sessions.data.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
              <div>
                <p className="text-sm font-medium">
                  {device(s.user_agent, t("security.sessions.unknownDevice"))}{" "}
                  {s.current && <Badge tone="primary">{t("security.sessions.thisBrowser")}</Badge>}
                </p>
                <p className="text-xs text-muted">
                  {t("security.sessions.signedInWith", { method: method(s.method), when: date(s.created_at) })} ·{" "}
                  {t("security.sessions.lastUsed", { when: relative(s.last_used_at) })}
                </p>
              </div>
              {!s.current && (
                <Button size="sm" variant="ghost" loading={revoke.isPending && revoke.variables === s.id} onClick={() => revoke.mutate(s.id)}>
                  {t("security.sessions.signOut")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** The signed-in user's own security settings: two-factor sign-in and sessions. */
export function Security() {
  const { t } = useTranslation();
  const { me } = useAuth();
  if (!me) return <Spinner />;
  return (
    <>
      <PageHeader title={t("security.title")} subtitle={t("security.subtitle")} />
      <div className="grid gap-6 lg:grid-cols-2">
        <TwoFactor me={me} />
        <Sessions />
      </div>
    </>
  );
}
