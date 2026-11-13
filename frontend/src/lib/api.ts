import { validationMessage } from "./validation";

// Same-origin "/api/v1" by default; a separately hosted API sets VITE_API_BASE_URL at build time.
const BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) || "/api/v1").replace(/\/+$/, "");
const CSRF_KEY = "procurax.csrf";

// The access token lives in memory only (15 minutes; gone when the tab closes). The session itself is an
// HttpOnly refresh cookie the page cannot read; /auth/refresh turns it into a new access token (ADR-019).
let accessToken: string | null = null;
export const tokenStore = {
  get: (): string | null => accessToken,
  set: (token: string | null) => {
    accessToken = token;
  },
};

// The session's CSRF token, sent as X-CSRF-Token to the cookie endpoints. Another site cannot read it, and
// it is worthless without the cookie, so localStorage is fine (it lets a new tab resume the session).
export const csrfStore = {
  get: (): string | null => {
    try {
      return localStorage.getItem(CSRF_KEY);
    } catch {
      return null;
    }
  },
  set: (token: string | null) => {
    try {
      if (token) localStorage.setItem(CSRF_KEY, token);
      else localStorage.removeItem(CSRF_KEY);
    } catch {
      /* storage unavailable: the session lasts for this page only */
    }
  },
};

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  requestId: string | null;

  constructor(status: number, code: string, message: string, details: unknown, requestId: string | null) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  /** Field-level messages for 422 validation errors, keyed by the last path segment. */
  fieldErrors(): Record<string, string> {
    if (this.code !== "validation_error" || !Array.isArray(this.details)) return {};
    const out: Record<string, string> = {};
    for (const d of this.details as { loc: (string | number)[]; msg: string; type?: string }[]) {
      out[String(d.loc[d.loc.length - 1])] = validationMessage(d.type, d.msg);
    }
    return out;
  }
}

let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: () => void) => {
  onUnauthorized = fn;
};

type Refreshed = { access_token: string; csrf_token?: string | null };
let onRefreshed: ((res: never) => void) | null = null;
/** Told about every renewed session (the auth context keeps the signed-in user in step). */
export const setRefreshedHandler = <T extends Refreshed>(fn: (res: T) => void) => {
  onRefreshed = fn as (res: never) => void;
};

let refreshing: Promise<Refreshed | null> | null = null;

/** Exchange the refresh cookie for a new access token. Concurrent callers share one request. */
export function refreshSession<T extends Refreshed>(): Promise<T | null> {
  if (refreshing) return refreshing as Promise<T | null>;
  const csrf = csrfStore.get();
  if (!csrf) return Promise.resolve(null);
  refreshing = (async () => {
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        const res = await fetch(buildUrl("/auth/refresh"), {
          method: "POST",
          headers: { Accept: "application/json", "X-CSRF-Token": csrf },
          credentials: "include",
        });
        const payload = await res.json().catch(() => null);
        if (res.ok) {
          tokenStore.set(payload.access_token);
          if (payload.csrf_token) csrfStore.set(payload.csrf_token);
          onRefreshed?.(payload as never);
          return payload as Refreshed;
        }
        // Another tab renewed the session a moment ago; the browser already has the new cookie.
        if (payload?.error?.details?.reason === "session_superseded" && attempt === 0) {
          await new Promise((resolve) => setTimeout(resolve, 300));
          continue;
        }
        if (res.status === 401) csrfStore.set(null);
        return null;
      }
      return null;
    } catch {
      return null; // offline: keep the stored token and let the next request try again
    } finally {
      refreshing = null;
    }
  })();
  return refreshing as Promise<T | null>;
}

/** Sign this browser out on the server (best effort) and forget the tokens. */
export async function endSession(): Promise<void> {
  const csrf = csrfStore.get();
  tokenStore.set(null);
  csrfStore.set(null);
  if (!csrf) return;
  await fetch(buildUrl("/auth/logout"), { method: "POST", headers: { "X-CSRF-Token": csrf }, credentials: "include" }).catch(() => undefined);
}

const NO_RETRY = ["/auth/login", "/auth/refresh", "/auth/logout", "/auth/mfa/verify", "/auth/register", "/auth/demo-login", "/auth/accept-invitation"];

type Query = Record<string, string | number | boolean | string[] | undefined | null>;

/** Absolute or same-origin URL of an API path (for full-page navigations such as single sign-on). */
export function apiUrl(path: string): string {
  return buildUrl(path);
}

function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => params.append(key, v));
    else params.set(key, String(value));
  }
  const qs = params.toString();
  return `${BASE}${path}${qs ? `?${qs}` : ""}`;
}

/** Sends the request; on 401 renews the session once from the refresh cookie and tries again. */
async function send(path: string, init: RequestInit, query?: Query): Promise<{ res: Response; hadToken: boolean }> {
  const run = () => {
    const headers = new Headers(init.headers);
    const token = tokenStore.get();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(buildUrl(path, query), { ...init, headers, credentials: "include" });
  };
  const hadToken = tokenStore.get() !== null;
  let res = await run();
  if (res.status === 401 && !NO_RETRY.includes(path) && csrfStore.get() && (await refreshSession())) {
    res = await run();
  }
  return { res, hadToken };
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const { res, hadToken } = await send(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }, query);
  if (res.status === 204) return undefined as T;
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const err = payload?.error ?? {};
    if (res.status === 401 && hadToken && !NO_RETRY.includes(path)) onUnauthorized?.();
    throw new ApiError(
      res.status,
      err.code ?? "http_error",
      err.message ?? `Request failed (${res.status})`,
      err.details,
      payload?.request_id ?? res.headers.get("X-Request-ID"),
    );
  }
  return payload as T;
}

/** A file download that needs the Authorization header (so it cannot be a plain link). */
async function requestBlob(path: string, query?: Query): Promise<Blob> {
  const { res, hadToken } = await send(path, {}, query);
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const err = payload?.error ?? {};
    if (res.status === 401 && hadToken) onUnauthorized?.();
    throw new ApiError(res.status, err.code ?? "http_error", err.message ?? `Request failed (${res.status})`, err.details, payload?.request_id ?? null);
  }
  return res.blob();
}

export const api = {
  blob: (path: string, query?: Query) => requestBlob(path, query),
  get: <T>(path: string, query?: Query) => request<T>("GET", path, undefined, query),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body ?? {}),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
  del: <T>(path: string) => request<T>("DELETE", path),
};

