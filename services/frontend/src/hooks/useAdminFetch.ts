import { useCallback, useEffect, useRef, useState } from "react";
import { adminFetch, AdminUnauthorizedError } from "../api";

/**
 * Admin variant of useFetch — identical state shape, but:
 *   - uses adminFetch (bearer header)
 *   - on 401 the hook returns a typed error so pages can force logout
 *   - supports optional polling via `pollMs`; clears on unmount
 *
 * Keeps the rest of the codebase on the existing useFetch conventions;
 * only admin components reach for this one.
 */
export interface AdminFetchState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  refresh(): void;
}

export function useAdminFetch<T>(path: string | null, opts?: { pollMs?: number }): AdminFetchState<T> {
  const [state, setState] = useState<AdminFetchState<T>>({
    data: null, error: null, loading: !!path, refresh: () => {},
  });
  const reqIdRef = useRef(0);
  const pollMs = opts?.pollMs;

  const run = useCallback(async () => {
    if (!path) return;
    const id = ++reqIdRef.current;
    setState(s => ({ ...s, loading: true }));
    try {
      const data = await adminFetch<T>(path);
      if (id !== reqIdRef.current) return;
      setState(s => ({ ...s, data, error: null, loading: false }));
    } catch (err) {
      if (id !== reqIdRef.current) return;
      setState(s => ({
        ...s,
        data: null,
        error: err instanceof Error ? err : new Error(String(err)),
        loading: false,
      }));
    }
  }, [path]);

  useEffect(() => {
    run();
    if (!pollMs) return;
    const t = setInterval(run, pollMs);
    return () => clearInterval(t);
  }, [run, pollMs]);

  return { ...state, refresh: run };
}

export { AdminUnauthorizedError };
