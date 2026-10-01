import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler, tokenStore } from "../lib/api";
import type { Me, TokenResponse } from "../lib/types";
import { AuthContext, type AuthState } from "./AuthContextValue";

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [token, setToken] = useState<string | null>(() => tokenStore.get());

  const meQuery = useQuery({
    queryKey: ["me", token],
    queryFn: () => api.get<Me>("/auth/me"),
    enabled: Boolean(token),
    staleTime: 60_000,
    retry: false,
  });

  const signOut = useCallback(() => {
    tokenStore.set(null);
    setToken(null);
    qc.clear();
  }, [qc]);

  const signIn = useCallback(
    (res: TokenResponse) => {
      qc.clear();
      tokenStore.set(res.access_token);
      setToken(res.access_token);
      qc.setQueryData(["me", res.access_token], res.user);
    },
    [qc],
  );

  useEffect(() => setUnauthorizedHandler(signOut), [signOut]);

  const value = useMemo<AuthState>(() => {
    const me = token ? (meQuery.data ?? null) : null;
    const perms = new Set(me?.permissions ?? []);
    const roles = new Set(me?.roles ?? []);
    return {
      me,
      loading: Boolean(token) && meQuery.isLoading,
      signIn,
      signOut,
      can: (p) => perms.has(p),
      hasRole: (r) => roles.has(r),
    };
  }, [token, meQuery.data, meQuery.isLoading, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}



