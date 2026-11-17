import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { AuthContext, type AuthState } from "../auth/AuthContextValue";
import type { Me } from "../lib/types";

export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    id: "u-1",
    email: "pat@example.com",
    full_name: "Pat Approver",
    job_title: "Manager",
    department_id: "d-1",
    manager_id: null,
    roles: ["manager", "employee"],
    permissions: ["approval:act", "purchase_request:create", "purchase_request:read_own", "vendor:read"],
    organization: {
      id: "o-1",
      name: "Test Co",
      slug: "test-co",
      base_currency: "JPY",
      country: "JP",
      fiscal_year_start_month: 4,
      timezone: "Asia/Tokyo",
      is_demo: false,
      created_at: "2026-01-01T00:00:00Z",
    },
    locale: null,
    ...overrides,
  };
}

/** Renders `ui` at `route` inside the app's providers, with a fixed signed-in user (or none). */
export function renderWithProviders(ui: ReactElement, { me = makeMe(), path = "/", route = "/" }: { me?: Me | null; path?: string; route?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const permissions = new Set(me?.permissions ?? []);
  const roles = new Set(me?.roles ?? []);
  const auth: AuthState = {
    me,
    loading: false,
    signIn: vi.fn(),
    signOut: vi.fn(),
    updateMe: vi.fn(),
    can: (p) => permissions.has(p),
    hasRole: (r) => roles.has(r),
  };
  const result = render(
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={auth}>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route path={path} element={ui} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
  return { ...result, auth, client };
}

type Handler = (body: unknown, url: URL) => unknown;

/**
 * Replaces fetch with a table of `"METHOD /path"` handlers (path without the /api/v1 prefix).
 * A handler returns a JSON body, or a Response for errors. Unknown routes return 404.
 */
export function mockApi(handlers: Record<string, Handler>) {
  const calls: { key: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace(/^\/api\/v1/, "")}`;
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ key, body });
    const handler = handlers[key];
    if (!handler) return jsonResponse({ error: { code: "not_found", message: `No mock for ${key}` } }, 404);
    const result = handler(body, url);
    return result instanceof Response ? result : jsonResponse(result, 200);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function apiError(status: number, code: string, message: string, details?: unknown): Response {
  return jsonResponse({ error: { code, message, details }, request_id: "req-123456789012" }, status);
}
