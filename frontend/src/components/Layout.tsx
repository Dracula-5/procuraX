import clsx from "clsx";
import {
  BookOpenCheck,
  Boxes,
  Building2,
  ClipboardCheck,
  Coins,
  FileSignature,
  FileText,
  Gauge,
  KeyRound,
  Landmark,
  LogOut,
  Menu,
  MessageSquareText,
  ScrollText,
  ShieldCheck,
  Truck,
  UserRoundCog,
  Users,
  Wallet,
  X,
} from "lucide-react";
import { Suspense, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { useLabels } from "../lib/labels";
import { DeploymentNotice } from "./DeploymentNotice";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { NotificationBell } from "./NotificationBell";
import { Badge, Spinner } from "./ui";

interface NavItem {
  to: string;
  key: "dashboard" | "requests" | "approvals" | "delegations" | "vendors" | "p2p" | "assistant" | "budgets" | "policy" | "paymentSettings" | "contracts" | "exchangeRates" | "ledger" | "audit" | "users" | "structure";
  icon: ReactNode;
  /** Shown when the user holds any of these permissions (none = everyone). */
  anyOf?: string[];
}

const NAV: { section: "procurement" | "finance" | "admin"; items: NavItem[] }[] = [
  {
    section: "procurement",
    items: [
      { to: "/app", key: "dashboard", icon: <Gauge className="size-4" /> },
      { to: "/app/requests", key: "requests", icon: <FileText className="size-4" /> },
      { to: "/app/approvals", key: "approvals", icon: <ClipboardCheck className="size-4" />, anyOf: ["approval:act"] },
      { to: "/app/approvals/delegations", key: "delegations", icon: <UserRoundCog className="size-4" />, anyOf: ["approval:act"] },
      { to: "/app/vendors", key: "vendors", icon: <Truck className="size-4" />, anyOf: ["vendor:read"] },
      { to: "/app/contracts", key: "contracts", icon: <FileSignature className="size-4" />, anyOf: ["vendor:read"] },
      { to: "/app/p2p", key: "p2p", icon: <Boxes className="size-4" />, anyOf: ["purchase_request:read_all"] },
      { to: "/app/assistant", key: "assistant", icon: <MessageSquareText className="size-4" />, anyOf: ["analytics:read"] },
    ],
  },
  {
    section: "finance",
    items: [
      { to: "/app/budgets", key: "budgets", icon: <Wallet className="size-4" />, anyOf: ["budget:read"] },
      { to: "/app/policy", key: "policy", icon: <ShieldCheck className="size-4" />, anyOf: ["policy:read"] },
      { to: "/app/admin/payment-settings", key: "paymentSettings", icon: <Landmark className="size-4" />, anyOf: ["organization:manage", "payment:approve"] },
      { to: "/app/fx", key: "exchangeRates", icon: <Coins className="size-4" />, anyOf: ["budget:read", "organization:manage"] },
      { to: "/app/ledger", key: "ledger", icon: <BookOpenCheck className="size-4" />, anyOf: ["organization:manage", "payment:approve"] },
      { to: "/app/audit", key: "audit", icon: <ScrollText className="size-4" />, anyOf: ["audit:read"] },
    ],
  },
  {
    section: "admin",
    items: [
      { to: "/app/admin/users", key: "users", icon: <Users className="size-4" />, anyOf: ["user:manage"] },
      { to: "/app/admin/structure", key: "structure", icon: <Building2 className="size-4" />, anyOf: ["org_structure:manage"] },
    ],
  },
];

export function Layout() {
  const { t } = useTranslation();
  const labels = useLabels();
  const { me, can, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  if (!me) return null;

  const sidebar = (
    <nav className="flex h-full flex-col gap-6 overflow-y-auto p-4" aria-label={t("nav.main")}>
      <div className="flex items-center gap-2 px-2">
        <img src="/favicon.svg" alt="" className="size-7" />
        <span className="text-base font-semibold tracking-tight">ProcuraX</span>
      </div>
      {NAV.map(({ section, items }) => {
        const visible = items.filter((i) => !i.anyOf || i.anyOf.some((p) => can(p)));
        if (!visible.length) return null;
        return (
          <div key={section}>
            <p className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-muted">{t(`nav.section.${section}`)}</p>
            <ul className="space-y-0.5">
              {visible.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === "/app" || item.to === "/app/approvals"}
                    onClick={() => setOpen(false)}
                    className={({ isActive }) =>
                      clsx(
                        "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm",
                        isActive ? "bg-primary-soft font-medium text-primary" : "text-fg hover:bg-surface-2",
                      )
                    }
                  >
                    {item.icon}
                    {t(`nav.item.${item.key}`)}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      <div className="mt-auto space-y-2 border-t border-border pt-4 text-sm">
        <div className="px-2">
          <p className="font-medium">{me.full_name}</p>
          <p className="truncate text-xs text-muted">{me.job_title ?? me.email}</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {me.roles.map((r) => (
              <Badge key={r} tone="primary">
                {labels.role(r)}
              </Badge>
            ))}
          </div>
        </div>
        <NavLink
          to="/app/security"
          onClick={() => setOpen(false)}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-muted hover:bg-surface-2 hover:text-fg"
        >
          <KeyRound className="size-4" /> {t("security.navLink")}
        </NavLink>
        <button onClick={signOut} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-muted hover:bg-surface-2 hover:text-fg">
          <LogOut className="size-4" /> {t("layout.signOut")}
        </button>
      </div>
    </nav>
  );

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[248px_1fr]">
      <aside className="sticky top-0 hidden h-screen border-r border-border bg-surface lg:block">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[85%] border-r border-border bg-surface">{sidebar}</aside>
        </div>
      )}
      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur lg:px-8">
          <button className="rounded-md p-1.5 text-muted hover:bg-surface-2 lg:hidden" onClick={() => setOpen((o) => !o)} aria-label={t("layout.toggleMenu")}>
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
          <p className="truncate text-sm font-medium">{me.organization.name}</p>
          {me.organization.is_demo && (
            <Badge tone="warn" className="ml-1">
              {t("layout.demoTenant")}
            </Badge>
          )}
          <span className="ml-auto hidden text-xs text-muted sm:inline">
            {t("layout.fyInfo", { month: me.organization.fiscal_year_start_month, currency: me.organization.base_currency })}
          </span>
          <div className="ml-auto flex items-center gap-2 sm:ml-0">
            <NotificationBell />
            <LanguageSwitcher />
          </div>
        </header>
        <DeploymentNotice />
        <main className="mx-auto max-w-7xl px-4 py-6 lg:px-8">
          <Suspense fallback={<Spinner />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
