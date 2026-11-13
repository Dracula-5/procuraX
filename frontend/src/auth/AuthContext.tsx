import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { applyLanguage, isLanguage } from "../i18n";
import { api, csrfStore, endSession, refreshSession, setRefreshedHandler, setUnauthorizedHandler, tokenStore } from "../lib/api";
import type { Me, TokenResponse } from "../lib/types";
import { AuthContext, type AuthState } from "./AuthContextValue";

/** After single sign-on the API redirects to /app#sso=<csrf token>; keep the token, drop the fragment. */
function takeSsoHandOver(): void {
  const match = /^#sso=([0-9a-f]{64})$/.exec(window.location.hash);
  if (!match) return;
  csrfStore.set(match[1]);
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [token, setToken] = useState<string | null>(() => tokenStore.get());
  // A stored CSRF token means this browser may still hold a session cookie: try to resume it first.
  const [resuming, setResuming] = useState(() => {
    takeSsoHandOver();
    return tokenStore.get() === null && csrfStore.get() !== null;
  });

  const meQuery = useQuery({
    queryKey: ["me", token],
    queryFn: () => api.get<Me>("/auth/me"),
    enabled: Boolean(token),
    staleTime: 60_000,
    retry: false,
  });

  const accept = useCallback(
    (res: TokenResponse) => {
      tokenStore.set(res.access_token);
      if (res.csrf_token) csrfStore.set(res.csrf_token);
      qc.setQueryData(["me", res.access_token], res.user);
      setToken(res.access_token);
    },
    [qc],
  );

  useEffect(() => {
    setRefreshedHandler<TokenResponse>(accept);
    if (!resuming) return;
    void refreshSession<TokenResponse>()
      .then((res) => res && accept(res))
      .finally(() => setResuming(false));
    // Runs once: resuming only starts true on first render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const forget = useCallback(() => {
    tokenStore.set(null);
    csrfStore.set(null);
    setToken(null);
    qc.clear();
  }, [qc]);

  const signOut = useCallback(() => {
    void endSession();
    forget();
  }, [forget]);

  const signIn = useCallback(
    (res: TokenResponse) => {
      qc.clear();
      accept(res);
    },
    [qc, accept],
  );

  const updateMe = useCallback((next: Me) => qc.setQueryData(["me", token], next), [qc, token]);

  // The session could not be renewed (expired, revoked elsewhere): drop local state only.
  useEffect(() => setUnauthorizedHandler(forget), [forget]);

  // A saved language preference follows the user to every device; without one the device's choice stays.
  const savedLocale = token ? meQuery.data?.locale : null;
  useEffect(() => {
    if (isLanguage(savedLocale)) void applyLanguage(savedLocale);
  }, [savedLocale]);

  const value = useMemo<AuthState>(() => {
    const me = token ? (meQuery.data ?? null) : null;
    const perms = new Set(me?.permissions ?? []);
    const roles = new Set(me?.roles ?? []);
    return {
      me,
      loading: resuming || (Boolean(token) && meQuery.isLoading),
      signIn,
      signOut,
      updateMe,
      can: (p) => perms.has(p),
      hasRole: (r) => roles.has(r),
    };
  }, [token, resuming, meQuery.data, meQuery.isLoading, signIn, signOut, updateMe]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
