import { useCallback, useEffect, useState } from "react";
import {
  ADMIN_TOKEN_STORAGE_KEY,
  verifyAdminToken,
  AdminDisabledError,
} from "../api";

/**
 * Single-user admin auth hook.
 *
 * Stores the bearer token in localStorage (not a cookie — we want per-
 * request opt-in via the Authorization header so the public API
 * remains CORS-friendly and no session magic sneaks onto the shared
 * domain).
 *
 * `login(token)` roundtrips through `verifyAdminToken` before
 * persisting — that way a pasted bad token fails fast at the login
 * page instead of being stored and then silently 401ing on the
 * dashboard. `logout()` clears storage; downstream fetches will
 * naturally 401 and push back to /admin/login.
 *
 * Because localStorage changes in other tabs aren't observed by default,
 * we listen for the storage event so logging out in tab A redirects
 * tab B on its next render.
 */
export interface AdminAuth {
  token: string | null;
  isAuthed: boolean;
  loading: boolean;
  error: string | null;
  login(token: string): Promise<boolean>;
  logout(): void;
}

export function useAdminAuth(): AdminAuth {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === ADMIN_TOKEN_STORAGE_KEY) {
        setToken(e.newValue);
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const login = useCallback(async (candidate: string) => {
    setLoading(true);
    setError(null);
    try {
      const ok = await verifyAdminToken(candidate);
      if (!ok) {
        setError("Invalid admin token.");
        return false;
      }
      localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, candidate);
      setToken(candidate);
      return true;
    } catch (e) {
      if (e instanceof AdminDisabledError) {
        setError("Admin panel is disabled on the server (ADMIN_TOKEN not set).");
      } else if (e instanceof Error) {
        setError(e.message);
      } else {
        setError("Login failed.");
      }
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
    setToken(null);
  }, []);

  return {
    token,
    isAuthed: !!token,
    loading,
    error,
    login,
    logout,
  };
}
