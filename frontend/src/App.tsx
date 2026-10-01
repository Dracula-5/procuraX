import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth/useAuth";
import { Layout } from "./components/Layout";
import { EmptyState, Spinner } from "./components/ui";
import { AcceptInvite } from "./pages/AcceptInvite";
import { Audit } from "./pages/Audit";
import { Assistant } from "./pages/Assistant";
import { Budgets } from "./pages/Budgets";
import { Dashboard } from "./pages/Dashboard";
import { Inbox } from "./pages/Inbox";
import { Landing } from "./pages/Landing";
import { Login } from "./pages/Login";
import { Policy } from "./pages/Policy";
import { PurchaseToPay } from "./pages/PurchaseToPay";
import { Register } from "./pages/Register";
import { RequestDetail } from "./pages/RequestDetail";
import { RequestForm } from "./pages/RequestForm";
import { Requests } from "./pages/Requests";
import { Vendors } from "./pages/Vendors";
import { Structure } from "./pages/admin/Structure";
import { UsersAdmin } from "./pages/admin/Users";

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Spinner label="Signing in" />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

/** UX guard only — the API enforces every permission independently. */
function RequirePermission({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(permission)) {
    return <EmptyState title="You don't have access to this page">Ask your organisation administrator for the required role.</EmptyState>;
  }
  return <>{children}</>;
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
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="requests" element={<Requests />} />
        <Route path="requests/new" element={<RequestForm />} />
        <Route path="requests/:id" element={<RequestDetail />} />
        <Route path="requests/:id/edit" element={<RequestForm />} />
        <Route path="approvals" element={<RequirePermission permission="approval:act"><Inbox /></RequirePermission>} />
        <Route path="vendors" element={<RequirePermission permission="vendor:read"><Vendors /></RequirePermission>} />
        <Route path="p2p" element={<RequirePermission permission="purchase_request:read_all"><PurchaseToPay /></RequirePermission>} />
        <Route path="assistant" element={<RequirePermission permission="analytics:read"><Assistant /></RequirePermission>} />
        <Route path="budgets" element={<RequirePermission permission="budget:read"><Budgets /></RequirePermission>} />
        <Route path="policy" element={<RequirePermission permission="policy:read"><Policy /></RequirePermission>} />
        <Route path="audit" element={<RequirePermission permission="audit:read"><Audit /></RequirePermission>} />
        <Route path="admin/users" element={<RequirePermission permission="user:manage"><UsersAdmin /></RequirePermission>} />
        <Route path="admin/structure" element={<RequirePermission permission="org_structure:manage"><Structure /></RequirePermission>} />
        <Route path="*" element={<EmptyState title="Page not found" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

