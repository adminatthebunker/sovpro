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
 * Admin-scoped fetch. Uses the same session cookie + CSRF token as
 * userFetch — admin access is "signed-in user with is_admin=true"
 * (enforced by requireAdmin on the server). The thin wrapper exists
 * so callers can write `adminFetch("/jobs")` without the /admin
 * prefix bookkeeping.
 *
 * 401 → not signed in; UI should redirect to /login.
 * 403 → signed in but not an admin (distinct from 401).
 * 503 → JWT_SECRET unset on the server (feature disabled).
 */
export class AdminUnauthorizedError extends Error {
  constructor() { super("not signed in"); }
}
export class AdminForbiddenError extends Error {
  constructor() { super("admin access required"); }
}
export class AdminDisabledError extends Error {
  constructor() { super("user accounts disabled on server (JWT_SECRET not set)"); }
}

export async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await userFetch<T>(`/admin${path}`, init);
  } catch (e) {
    if (e instanceof UserUnauthorizedError) throw new AdminUnauthorizedError();
    if (e instanceof UserAuthDisabledError) throw new AdminDisabledError();
    // Fastify returns 403 for both "non-admin" and "CSRF fail". userFetch
    // surfaces a plain Error for 403; re-type as AdminForbiddenError so
    // UI can distinguish it from a generic failure.
    if (e instanceof Error && /^403\b/.test(e.message)) {
      throw new AdminForbiddenError();
    }
    throw e;
  }
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
  is_admin: boolean;
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
    /** Legacy singular pin — read-side only; new writes use politician_ids. */
    politician_id?: string;
    politician_ids?: string[];
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
  /** Present only on /me/corrections — 0 when no reward has landed. */
  credits_earned?: number;
}

