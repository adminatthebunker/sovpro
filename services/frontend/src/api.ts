const BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${path}`);
  }
  return (await res.json()) as T;
}

/**
 * Admin-scoped fetch that auto-attaches Authorization: Bearer <token>.
 *
 * 401 → throws AdminUnauthorizedError so callers (hooks, pages) can
 * decide whether to logout. 503 → "admin panel disabled on server"; we
 * surface that verbatim so the login page can tell the user ADMIN_TOKEN
 * isn't set instead of a generic "login failed".
 */
export const ADMIN_TOKEN_STORAGE_KEY = "sw_admin_token";

export class AdminUnauthorizedError extends Error {
  constructor() { super("admin session expired"); }
}
export class AdminDisabledError extends Error {
  constructor() { super("admin panel disabled on server (ADMIN_TOKEN not set)"); }
}

export async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY);
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init?.headers as Record<string, string> ?? {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}/admin${path}`, { ...init, headers });
  if (res.status === 401) throw new AdminUnauthorizedError();
  if (res.status === 503) throw new AdminDisabledError();
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body || path}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

/** Verify a pasted token against /admin/login; resolves true on 200, false on 401. */
export async function verifyAdminToken(token: string): Promise<boolean> {
  const res = await fetch(`${BASE}/admin/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ token }),
  });
  if (res.status === 401) return false;
  if (res.status === 503) throw new AdminDisabledError();
  return res.ok;
}
