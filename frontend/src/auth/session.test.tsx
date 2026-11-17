import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, csrfStore, refreshSession, setUnauthorizedHandler, tokenStore } from "../lib/api";
import { Login } from "../pages/Login";
import { apiError, makeMe, mockApi, renderWithProviders } from "../test/utils";
import { AuthProvider } from "./AuthContext";
import { useAuth } from "./useAuth";

const CSRF = "c".repeat(64);
const tokens = (access: string) => ({ access_token: access, token_type: "bearer", expires_in: 900, csrf_token: CSRF, user: makeMe() });

afterEach(() => {
  tokenStore.set(null);
  window.history.replaceState(null, "", "/");
});

describe("session client", () => {
  it("keeps the access token in memory only and renews it once on a 401", async () => {
    tokenStore.set("expired");
    csrfStore.set(CSRF);
    let me = 0;
    const { calls } = mockApi({
      "GET /auth/me": () => (++me === 1 ? apiError(401, "unauthenticated", "Invalid or expired token") : makeMe()),
      "POST /auth/refresh": () => tokens("fresh"),
    });
    await expect(api.get("/auth/me")).resolves.toMatchObject({ id: "u-1" });
    expect(calls.map((c) => c.key)).toEqual(["GET /auth/me", "POST /auth/refresh", "GET /auth/me"]);
    expect(tokenStore.get()).toBe("fresh");
    expect(sessionStorage.length).toBe(0); // nothing about the session is kept in web storage but the CSRF token
    expect(localStorage.getItem("procurax.csrf")).toBe(CSRF);
  });

  it("shares one refresh between concurrent requests and sends the CSRF header", async () => {
    csrfStore.set(CSRF);
    const fetchMock = mockApi({ "POST /auth/refresh": () => tokens("shared") }).fetchMock;
    const [a, b] = await Promise.all([refreshSession(), refreshSession()]);
    expect(a).toEqual(b);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe(CSRF);
    expect(init.credentials).toBe("include");
  });

  it("signs out locally when the session cannot be renewed", async () => {
    tokenStore.set("expired");
    csrfStore.set(CSRF);
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    mockApi({
      "GET /purchase-requests": () => apiError(401, "unauthenticated", "Invalid or expired token"),
      "POST /auth/refresh": () => apiError(401, "unauthenticated", "This session was signed out", { reason: "session_revoked" }),
    });
    await expect(api.get("/purchase-requests")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(csrfStore.get()).toBeNull();
  });
});

function Whoami() {
  const { me, loading } = useAuth();
  return <p>{loading ? "loading" : (me?.full_name ?? "signed out")}</p>;
}

function renderProvider() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <Whoami />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("AuthProvider", () => {
  it("resumes the session from the refresh cookie after a reload", async () => {
    csrfStore.set(CSRF);
    mockApi({ "POST /auth/refresh": () => tokens("resumed"), "GET /auth/me": () => makeMe() });
    renderProvider();
    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(await screen.findByText("Pat Approver")).toBeInTheDocument();
  });

  it("takes the CSRF token from the single sign-on redirect and drops the fragment", async () => {
    window.history.replaceState(null, "", `/app#sso=${CSRF}`);
    const { calls } = mockApi({ "POST /auth/refresh": () => tokens("sso"), "GET /auth/me": () => makeMe() });
    renderProvider();
    expect(await screen.findByText("Pat Approver")).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    expect(calls[0].key).toBe("POST /auth/refresh");
  });

  it("starts signed out without a stored session", async () => {
    const { calls } = mockApi({});
    renderProvider();
    expect(await screen.findByText("signed out")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });
});

describe("sign-in page", () => {
  it("asks for the authenticator code after the password when two-factor sign-in is on", async () => {
    const { calls } = mockApi({
      "GET /auth/config": () => ({ registration_enabled: true, demo_mode: false, oidc_enabled: false, oidc_display_name: null, mfa_available: true }),
      "GET /auth/demo-personas": () => [],
      "GET /meta": () => ({ registration_enabled: true }),
      "POST /auth/login": () => ({ mfa_required: true, mfa_token: "challenge-token-value-123", expires_in: 300 }),
      "POST /auth/mfa/verify": () => tokens("after-mfa"),
    });
    const { auth } = renderWithProviders(<Login />, { me: null, path: "/login", route: "/login" });
    await userEvent.type(screen.getByLabelText("E-mail"), "pat@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "Str0ngPassw0rd1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your code")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("6-digit code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(auth.signIn).toHaveBeenCalled());
    expect(calls.find((c) => c.key === "POST /auth/mfa/verify")?.body).toEqual({ mfa_token: "challenge-token-value-123", code: "123456" });
  });

  it("offers single sign-on when configured and explains a refused one", async () => {
    mockApi({
      "GET /auth/config": () => ({ registration_enabled: true, demo_mode: false, oidc_enabled: true, oidc_display_name: "Contoso Entra ID", mfa_available: true }),
      "GET /auth/demo-personas": () => [],
      "GET /meta": () => ({ registration_enabled: true }),
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/login?sso_error=sso_no_account"]}>
          <Routes>
            <Route path="/login" element={<LoginWithAuth />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("button", { name: "Continue with Contoso Entra ID" })).toBeInTheDocument();
    expect(screen.getByText(/no active ProcuraX account for this e-mail/)).toBeInTheDocument();
  });
});

function LoginWithAuth() {
  return (
    <AuthProvider>
      <Login />
    </AuthProvider>
  );
}
