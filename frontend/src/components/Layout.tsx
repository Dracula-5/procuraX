import clsx from "clsx";
import {
  Building2,
  Boxes,
  ClipboardCheck,
  FileText,
  Gauge,
  MessageSquareText,
  LogOut,
  Menu,
  ScrollText,
  ShieldCheck,
  Truck,
  Users,
  Wallet,
  X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { label, ROLE_LABELS } from "../lib/format";
import { Badge } from "./ui";
import { DeploymentNotice } from "./DeploymentNotice";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  permission?: string;
}

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Procurement",
    items: [
      { to: "/app", label: "Dashboard", icon: <Gauge className="size-4" /> },
      { to: "/app/requests", label: "Purchase requests", icon: <FileText className="size-4" /> },
      { to: "/app/approvals", label: "Approval inbox", icon: <ClipboardCheck className="size-4" />, permission: "approval:act" },
      { to: "/app/vendors", label: "Vendors", icon: <Truck className="size-4" />, permission: "vendor:read" },
      { to: "/app/p2p", label: "Purchase to pay", icon: <Boxes className="size-4" />, permission: "purchase_request:read_all" },
      { to: "/app/assistant", label: "Procurement assistant", icon: <MessageSquareText className="size-4" />, permission: "analytics:read" },
    ],
  },
  {
    section: "Finance & control",
    items: [
      { to: "/app/budgets", label: "Budgets", icon: <Wallet className="size-4" />, permission: "budget:read" },
      { to: "/app/policy", label: "Approval policy", icon: <ShieldCheck className="size-4" />, permission: "policy:read" },
      { to: "/app/audit", label: "Audit trail", icon: <ScrollText className="size-4" />, permission: "audit:read" },
    ],
  },
  {
    section: "Administration",
    items: [
      { to: "/app/admin/users", label: "Users & roles", icon: <Users className="size-4" />, permission: "user:manage" },
      { to: "/app/admin/structure", label: "Organisation", icon: <Building2 className="size-4" />, permission: "org_structure:manage" },
    ],
  },
];

export function Layout() {
  const { me, can, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  if (!me) return null;

  const sidebar = (
    <nav className="flex h-full flex-col gap-6 overflow-y-auto p-4" aria-label="Main">
      <div className="flex items-center gap-2 px-2">
        <img src="/favicon.svg" alt="" className="size-7" />
        <span className="text-base font-semibold tracking-tight">ProcuraX</span>
      </div>
      {NAV.map(({ section, items }) => {
        const visible = items.filter((i) => !i.permission || can(i.permission));
        if (!visible.length) return null;
        return (
          <div key={section}>
            <p className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-muted">{section}</p>
            <ul className="space-y-0.5">
              {visible.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === "/app"}
                    onClick={() => setOpen(false)}
                    className={({ isActive }) =>
                      clsx(
                        "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm",
                        isActive ? "bg-primary-soft font-medium text-primary" : "text-fg hover:bg-surface-2",
                      )
                    }
                  >
                    {item.icon}
                    {item.label}
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
                {label(ROLE_LABELS, r)}
              </Badge>
            ))}
          </div>
        </div>
        <button onClick={signOut} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-muted hover:bg-surface-2 hover:text-fg">
          <LogOut className="size-4" /> Sign out
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
          <button className="rounded-md p-1.5 text-muted hover:bg-surface-2 lg:hidden" onClick={() => setOpen((o) => !o)} aria-label="Toggle menu">
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
          <p className="truncate text-sm font-medium">{me.organization.name}</p>
          {me.organization.is_demo && (
            <Badge tone="warn" className="ml-1">
              Demo tenant · fictional data
            </Badge>
          )}
          <span className="ml-auto hidden text-xs text-muted sm:inline">
            FY starts month {me.organization.fiscal_year_start_month} · {me.organization.base_currency}
          </span>
        </header>
        <DeploymentNotice />
        <main className="mx-auto max-w-7xl px-4 py-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

