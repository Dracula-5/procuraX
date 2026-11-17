import { ArrowRight, CheckCircle2, CircleDashed, FileSpreadsheet, Mail, Stamp, Workflow } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { DeploymentNotice } from "../components/DeploymentNotice";
import { LanguageSwitcher } from "../components/LanguageSwitcher";
import { useMeta } from "../lib/meta";

const PROBLEMS: { icon: ReactNode; key: "email" | "chains" | "spreadsheets" | "controls" }[] = [
  { icon: <Mail className="size-5" />, key: "email" },
  { icon: <Stamp className="size-5" />, key: "chains" },
  { icon: <FileSpreadsheet className="size-5" />, key: "spreadsheets" },
  { icon: <Workflow className="size-5" />, key: "controls" },
];

// Mirrors docs/transformation_roadmap.md: "done" means implemented and tested, possibly partial.
const STATUS: { done: boolean; key: "intake" | "policy" | "approvals" | "isolation" | "p2p" | "ai" }[] = [
  { done: true, key: "intake" },
  { done: true, key: "policy" },
  { done: true, key: "approvals" },
  { done: true, key: "isolation" },
  { done: true, key: "p2p" },
  { done: false, key: "ai" },
];

const PRINCIPLES = ["recommend", "enforce", "decide"] as const;

export function Landing() {
  const { t } = useTranslation();
  const { me } = useAuth();
  const registration = useMeta().data?.registration_enabled !== false;
  return (
    <div className="min-h-screen">
      <DeploymentNotice />
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5">
        <div className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="size-7" />
          <span className="font-semibold tracking-tight">ProcuraX</span>
        </div>
        <nav className="flex items-center gap-2 text-sm">
          <LanguageSwitcher />
          {me ? (
            <Link to="/app" className="rounded-md bg-primary px-3 py-2 font-medium text-primary-fg hover:bg-primary-hover">
              {t("landing.openWorkspace")}
            </Link>
          ) : (
            <>
              <Link to="/login" className="rounded-md px-3 py-2 text-muted hover:text-fg">
                {t("auth.signIn")}
              </Link>
              {registration && (
                <Link to="/register" className="rounded-md bg-primary px-3 py-2 font-medium text-primary-fg hover:bg-primary-hover">
                  {t("landing.createOrg")}
                </Link>
              )}
            </>
          )}
        </nav>
      </header>

      <section className="mx-auto max-w-6xl px-4 pb-16 pt-10 sm:pt-16">
        <p className="text-sm font-medium text-primary">{t("landing.eyebrow")}</p>
        <h1 className="mt-3 max-w-3xl text-3xl font-semibold tracking-tight sm:text-5xl">{t("landing.headline")}</h1>
        <p className="mt-5 max-w-2xl text-base text-muted sm:text-lg">{t("landing.lead")}</p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link to="/login" className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-fg hover:bg-primary-hover">
            {t("landing.tryDemo")} <ArrowRight className="size-4" />
          </Link>
          {registration && (
            <Link to="/register" className="rounded-md border border-border bg-surface px-4 py-2.5 text-sm font-medium hover:bg-surface-2">
              {t("landing.ownOrg")}
            </Link>
          )}
        </div>
      </section>

      <section className="border-y border-border bg-surface">
        <div className="mx-auto max-w-6xl px-4 py-14">
          <h2 className="text-lg font-semibold">{t("landing.currentState")}</h2>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {PROBLEMS.map((p) => (
              <div key={p.key} className="rounded-lg border border-border p-4">
                <div className="text-primary">{p.icon}</div>
                <p className="mt-3 text-sm font-semibold">{t(`landing.problems.${p.key}.title`)}</p>
                <p className="mt-1 text-sm text-muted">{t(`landing.problems.${p.key}.body`)}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-14">
        <h2 className="text-lg font-semibold">{t("landing.principle")}</h2>
        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          {PRINCIPLES.map((key, i) => (
            <div key={key} className="rounded-lg border border-border bg-surface p-5">
              <p className="text-xs font-semibold text-muted">0{i + 1}</p>
              <p className="mt-1 font-semibold">{t(`landing.principles.${key}.title`)}</p>
              <p className="mt-2 text-sm text-muted">{t(`landing.principles.${key}.body`)}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="border-t border-border bg-surface">
        <div className="mx-auto max-w-6xl px-4 py-14">
          <h2 className="text-lg font-semibold">{t("landing.builtToday")}</h2>
          <p className="mt-1 text-sm text-muted">{t("landing.builtNote")}</p>
          <ul className="mt-6 grid gap-3 sm:grid-cols-2">
            {STATUS.map((s) => (
              <li key={s.key} className="flex gap-3 rounded-lg border border-border p-4">
                {s.done ? <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-ok" /> : <CircleDashed className="mt-0.5 size-5 shrink-0 text-muted" />}
                <div>
                  <p className="text-sm font-semibold">
                    {t(`landing.status.${s.key}.title`)} {!s.done && <span className="font-normal text-muted">· {t("landing.roadmap")}</span>}
                  </p>
                  <p className="mt-1 text-sm text-muted">{t(`landing.status.${s.key}.body`)}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <footer className="mx-auto max-w-6xl px-4 py-8 text-xs text-muted">{t("landing.footer")}</footer>
    </div>
  );
}
