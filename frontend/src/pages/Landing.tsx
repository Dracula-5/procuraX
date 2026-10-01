import { ArrowRight, CheckCircle2, CircleDashed, FileSpreadsheet, Mail, Stamp, Workflow } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { DeploymentNotice } from "../components/DeploymentNotice";
import { useMeta } from "../lib/meta";

const PROBLEMS: { icon: ReactNode; title: string; body: string }[] = [
  { icon: <Mail className="size-5" />, title: "Requests arrive by e-mail", body: "No single intake, no status visibility, and no way to see what is already being bought." },
  { icon: <Stamp className="size-5" />, title: "Approvals are manual chains", body: "Paper or PDF ringi circulate person to person; thresholds are applied from memory." },
  { icon: <FileSpreadsheet className="size-5" />, title: "Budgets live in spreadsheets", body: "Commitments are reconciled after the fact, so overspend is found at month-end." },
  { icon: <Workflow className="size-5" />, title: "Controls are hard to prove", body: "Audit evidence is scattered across inboxes; split purchases and off-contract buying go unnoticed." },
];

const STATUS: { done: boolean; title: string; body: string }[] = [
  { done: true, title: "Digital intake & purchase requests", body: "Line items, cost centres, preferred vendor, drafts and resubmission." },
  { done: true, title: "Deterministic policy engine", body: "Versioned thresholds, vendor, contract, budget, split-purchase and emergency rules — every decision cites a rule ID." },
  { done: true, title: "Approval orchestration", body: "Sequential chains from the org structure and role pools, segregation of duties, SLA due dates." },
  { done: true, title: "Multi-tenant isolation & audit", body: "Application scoping plus PostgreSQL row-level security; append-only audit trail." },
  { done: false, title: "PO, goods receipt, invoice & 3-way match", body: "Next stages of the roadmap." },
  { done: false, title: "AI components", body: "Vendor ranking, invoice extraction, spend classification, anomaly detection, RAG assistant — each to be benchmarked against a baseline before any claim is made." },
];

export function Landing() {
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
          {me ? (
            <Link to="/app" className="rounded-md bg-primary px-3 py-2 font-medium text-primary-fg hover:bg-primary-hover">
              Open workspace
            </Link>
          ) : (
            <>
              <Link to="/login" className="rounded-md px-3 py-2 text-muted hover:text-fg">
                Sign in
              </Link>
              {registration && (
                <Link to="/register" className="rounded-md bg-primary px-3 py-2 font-medium text-primary-fg hover:bg-primary-hover">
                  Create organisation
                </Link>
              )}
            </>
          )}
        </nav>
      </header>

      <section className="mx-auto max-w-6xl px-4 pb-16 pt-10 sm:pt-16">
        <p className="text-sm font-medium text-primary">Procurement & spend transformation</p>
        <h1 className="mt-3 max-w-3xl text-3xl font-semibold tracking-tight sm:text-5xl">
          From e-mail, spreadsheets and stamp chains to a governed purchase-to-pay workflow.
        </h1>
        <p className="mt-5 max-w-2xl text-base text-muted sm:text-lg">
          ProcuraX digitises procurement intake and approvals, enforces purchasing policy deterministically, and keeps a
          complete audit trail — the foundation for invoice automation, spend intelligence and AI decision support.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link to="/login" className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-fg hover:bg-primary-hover">
            Try the demo tenant <ArrowRight className="size-4" />
          </Link>
          {registration && (
            <Link to="/register" className="rounded-md border border-border bg-surface px-4 py-2.5 text-sm font-medium hover:bg-surface-2">
              Start with your own organisation
            </Link>
          )}
        </div>
      </section>

      <section className="border-y border-border bg-surface">
        <div className="mx-auto max-w-6xl px-4 py-14">
          <h2 className="text-lg font-semibold">The current state it replaces</h2>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {PROBLEMS.map((p) => (
              <div key={p.title} className="rounded-lg border border-border p-4">
                <div className="text-primary">{p.icon}</div>
                <p className="mt-3 text-sm font-semibold">{p.title}</p>
                <p className="mt-1 text-sm text-muted">{p.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-14">
        <h2 className="text-lg font-semibold">Operating principle</h2>
        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          {[
            ["AI recommends", "Models rank vendors, extract invoices and flag anomalies — with confidence and evidence."],
            ["Rules enforce", "A deterministic, versioned policy engine decides routing and blocking. It is never overridden by a model."],
            ["Humans decide", "High-value, high-risk and exception cases always go to an accountable approver."],
          ].map(([title, body], i) => (
            <div key={title} className="rounded-lg border border-border bg-surface p-5">
              <p className="text-xs font-semibold text-muted">0{i + 1}</p>
              <p className="mt-1 font-semibold">{title}</p>
              <p className="mt-2 text-sm text-muted">{body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="border-t border-border bg-surface">
        <div className="mx-auto max-w-6xl px-4 py-14">
          <h2 className="text-lg font-semibold">What is built today</h2>
          <p className="mt-1 text-sm text-muted">Delivered in stages. Nothing below the line is claimed until it is built and measured.</p>
          <ul className="mt-6 grid gap-3 sm:grid-cols-2">
            {STATUS.map((s) => (
              <li key={s.title} className="flex gap-3 rounded-lg border border-border p-4">
                {s.done ? <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-ok" /> : <CircleDashed className="mt-0.5 size-5 shrink-0 text-muted" />}
                <div>
                  <p className="text-sm font-semibold">
                    {s.title} {!s.done && <span className="font-normal text-muted">· roadmap</span>}
                  </p>
                  <p className="mt-1 text-sm text-muted">{s.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <footer className="mx-auto max-w-6xl px-4 py-8 text-xs text-muted">
        Portfolio project. The demo tenant contains fictional companies, people and data.
      </footer>
    </div>
  );
}

