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

// ── End-user (magic-link) auth ──────────────────────────────────
//
// User auth uses an httpOnly `sw_session` cookie + non-httpOnly
// `sw_csrf` cookie (double-submit CSRF). The fetch helper below sends
// credentials on every request and attaches the CSRF header to
// mutating verbs. 503 means the feature is disabled server-side
// (JWT_SECRET unset), 401 means "not signed in or session expired".

export class UserUnauthorizedError extends Error {
  constructor() { super("not signed in"); }
}
export class UserAuthDisabledError extends Error {
  constructor() { super("user accounts disabled on server (JWT_SECRET not set)"); }
}

function readCsrfCookieValue(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)sw_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export async function userFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init?.headers as Record<string, string> ?? {}),
  };
  const needsCsrf = method !== "GET" && method !== "HEAD";
  if (needsCsrf) {
    const csrf = readCsrfCookieValue();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (res.status === 401) throw new UserUnauthorizedError();
  if (res.status === 503) throw new UserAuthDisabledError();
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body || path}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export interface MeResponse {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
  last_login_at: string | null;
}

export interface SavedSearch {
  id: string;
  user_id: string;
  name: string;
  filter_payload: {
    q?: string;
    lang?: "en" | "fr" | "any";
    level?: "federal" | "provincial" | "municipal";
    province_territory?: string;
    politician_id?: string;
    party?: string;
    from?: string;
    to?: string;
  };
  alert_cadence: "none" | "daily" | "weekly";
  last_checked_at: string | null;
  last_notified_at: string | null;
  created_at: string;
  updated_at: string;
  has_embedding: boolean;
  feed_url: string | null;
}

export interface CorrectionSubmission {
  id: string;
  subject_type: "speech" | "bill" | "politician" | "vote" | "organization" | "general";
  subject_id: string | null;
  issue: string;
  proposed_fix: string | null;
  evidence_url: string | null;
  status: "pending" | "triaged" | "applied" | "rejected" | "duplicate" | "spam";
  reviewer_notes: string | null;
  received_at: string;
  resolved_at: string | null;
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
