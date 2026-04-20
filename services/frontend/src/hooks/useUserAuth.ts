import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  userFetch,
  UserUnauthorizedError,
  UserAuthDisabledError,
  type MeResponse,
} from "../api";

/**
 * End-user auth state. Shared via a single provider at the app root so
 * the header's "signed in" indicator and any consumer page (SaveSearch,
 * FollowPolitician, /account/*) stay in lockstep — one GET /me per app
 * mount, one source of truth, no stale header after sign-in/out.
 *
 * Sessions live in an httpOnly cookie we can't read from JS, so this
 * hook's entire job is "does GET /me return a user?" plus a logout()
 * that POSTs to /me/logout (server clears the cookies, browser picks
 * that up automatically).
 *
 * Login is NOT done here. The magic-link flow is:
 *   LoginPage → POST /auth/request-link → user gets email → click link
 *     → VerifyPage → POST /auth/verify → cookie set → refresh() here.
 */

export interface UserAuth {
  user: MeResponse | null;
  loading: boolean;
  disabled: boolean;   // server returned 503 (JWT_SECRET unset)
  error: string | null;
  refresh(): Promise<void>;
  logout(): Promise<void>;
}

const UserAuthContext = createContext<UserAuth | null>(null);

export function UserAuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const me = await userFetch<MeResponse>("/me");
      setUser(me);
    } catch (e) {
      if (e instanceof UserUnauthorizedError) {
        setUser(null);
      } else if (e instanceof UserAuthDisabledError) {
        setUser(null);
        setDisabled(true);
      } else if (e instanceof Error) {
        setError(e.message);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await userFetch<void>("/me/logout", { method: "POST" });
    } catch {
      // Even if the call fails (network, 401), we clear local state —
      // the user intent is clear and the cookie will be re-checked on
      // the next request.
    }
    setUser(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value: UserAuth = { user, loading, disabled, error, refresh, logout };
  return createElement(UserAuthContext.Provider, { value }, children);
}

export function useUserAuth(): UserAuth {
  const ctx = useContext(UserAuthContext);
  if (!ctx) {
    throw new Error("useUserAuth must be used within a UserAuthProvider");
  }
  return ctx;
}
