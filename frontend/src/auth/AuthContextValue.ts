import { createContext } from "react";
import type { Me, TokenResponse } from "../lib/types";

export interface AuthState {
  me: Me | null;
  loading: boolean;
  signIn: (res: TokenResponse) => void;
  signOut: () => void;
  can: (permission: string) => boolean;
  hasRole: (role: string) => boolean;
}

export const AuthContext = createContext<AuthState | null>(null);

