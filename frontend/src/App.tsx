import { lazy, Suspense, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth/useAuth";
import { Layout } from "./components/Layout";
import { EmptyState, Spinner } from "./components/ui";
import { AcceptInvite } from "./pages/AcceptInvite";
import { Landing } from "./pages/Landing";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";

// Signed-in pages load on demand; the landing and sign-in pages stay in the main bundle.
const Assistant = lazy(() => import("./pages/Assistant").then((m) => ({ default: m.Assistant })));
const Audit = lazy(() => import("./pages/Audit").then((m) => ({ default: m.Audit })));
const Budgets = lazy(() => import("./pages/Budgets").then((m) => ({ default: m.Budgets })));
const Contracts = lazy(() => import("./pages/Contracts").then((m) => ({ default: m.Contracts })));
const Dashboard = lazy(() => import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const ExchangeRates = lazy(() => import("./pages/ExchangeRates").then((m) => ({ default: m.ExchangeRates })));
const Ledger = lazy(() => import("./pages/Ledger").then((m) => ({ default: m.Ledger })));
const Delegations = lazy(() => import("./pages/Delegations").then((m) => ({ default: m.Delegations })));
const Inbox = lazy(() => import("./pages/Inbox").then((m) => ({ default: m.Inbox })));
const Notifications = lazy(() => import("./pages/Notifications").then((m) => ({ default: m.Notifications })));
const PaymentSettings = lazy(() => import("./pages/PaymentSettings").then((m) => ({ default: m.PaymentSettings })));
const Policy = lazy(() => import("./pages/Policy").then((m) => ({ default: m.Policy })));
const PurchaseToPay = lazy(() => import("./pages/PurchaseToPay").then((m) => ({ default: m.PurchaseToPay })));
const RequestDetail = lazy(() => import("./pages/RequestDetail").then((m) => ({ default: m.RequestDetail })));
const RequestForm = lazy(() => import("./pages/RequestForm").then((m) => ({ default: m.RequestForm })));
const Requests = lazy(() => import("./pages/Requests").then((m) => ({ default: m.Requests })));
const VendorBankAccount = lazy(() => import("./pages/VendorBankAccount").then((m) => ({ default: m.VendorBankAccount })));
const Vendors = lazy(() => import("./pages/Vendors").then((m) => ({ default: m.Vendors })));
const Security = lazy(() => import("./pages/Security").then((m) => ({ default: m.Security })));
const Structure = lazy(() => import("./pages/admin/Structure").then((m) => ({ default: m.Structure })));
const UsersAdmin = lazy(() => import("./pages/admin/Users").then((m) => ({ default: m.UsersAdmin })));

function RequireAuth({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Spinner label={t("common.signingIn")} />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

/** UX guard only — the API enforces every permission independently. Passes with any one permission. */
export function RequirePermission({ permission, children }: { permission: string | string[]; children: ReactNode }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const needed = Array.isArray(permission) ? permission : [permission];
  if (!needed.some((p) => can(p))) {
    return <EmptyState title={t("app.noAccessTitle")}>{t("app.noAccessBody")}</EmptyState>;
  }
  return <>{children}</>;
}

function NotFound() {
  const { t } = useTranslation();
  return <EmptyState title={t("app.notFound")} />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/accept-invite" element={<AcceptInvite />} />
      <Route
        path="/app"
        element={
          <RequireAuth>
            <Suspense fallback={<Spinner />}>
              <Layout />
            </Suspense>
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="requests" element={<Requests />} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="security" element={<Security />} />
        <Route path="requests/new" element={<RequestForm />} />
        <Route path="requests/:id" element={<RequestDetail />} />
        <Route path="requests/:id/edit" element={<RequestForm />} />
        <Route path="approvals" element={<RequirePermission permission="approval:act"><Inbox /></RequirePermission>} />
        <Route path="approvals/delegations" element={<RequirePermission permission="approval:act"><Delegations /></RequirePermission>} />
        <Route path="vendors" element={<RequirePermission permission="vendor:read"><Vendors /></RequirePermission>} />
        <Route path="vendors/:id/bank-account" element={<RequirePermission permission="vendor:manage"><VendorBankAccount /></RequirePermission>} />
        <Route path="contracts" element={<RequirePermission permission="vendor:read"><Contracts /></RequirePermission>} />
        <Route path="fx" element={<ExchangeRates />} />
        <Route path="ledger" element={<RequirePermission permission={["organization:manage", "payment:approve"]}><Ledger /></RequirePermission>} />
        <Route path="p2p" element={<RequirePermission permission="purchase_request:read_all"><PurchaseToPay /></RequirePermission>} />
        <Route path="assistant" element={<RequirePermission permission="analytics:read"><Assistant /></RequirePermission>} />
        <Route path="budgets" element={<RequirePermission permission="budget:read"><Budgets /></RequirePermission>} />
        <Route path="policy" element={<RequirePermission permission="policy:read"><Policy /></RequirePermission>} />
        <Route path="audit" element={<RequirePermission permission="audit:read"><Audit /></RequirePermission>} />
        <Route path="admin/users" element={<RequirePermission permission="user:manage"><UsersAdmin /></RequirePermission>} />
        <Route path="admin/structure" element={<RequirePermission permission="org_structure:manage"><Structure /></RequirePermission>} />
        <Route
          path="admin/payment-settings"
          element={<RequirePermission permission={["organization:manage", "payment:approve"]}><PaymentSettings /></RequirePermission>}
        />
        <Route path="*" element={<NotFound />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
