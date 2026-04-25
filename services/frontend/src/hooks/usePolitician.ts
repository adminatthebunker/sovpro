import { useEffect, useState } from "react";
import { fetchJson } from "../api";

/**
 * Generic fetch state. Mirrors the shape useFetch uses so components feel
 * consistent across the codebase.
 */
export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  /** True when the endpoint returned 404 — used to show "not yet available"
   *  placeholders for sub-resources whose API routes haven't shipped yet
   *  (Phase 7a landing in parallel). */
  notFound: boolean;
}

function emptyState<T>(): AsyncState<T> {
  return { data: null, error: null, loading: true, notFound: false };
}

/**
 * Low-level fetch hook that tolerates 404s by surfacing `notFound: true`
 * rather than an error. Needed because Phase 7a's routes (/offices,
 * /committees) may not be merged yet — the detail page has to degrade
 * gracefully tab-by-tab.
 */
function useTolerantFetch<T>(path: string | null): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>(emptyState<T>());

  useEffect(() => {
    if (!path) {
      setState({ data: null, error: null, loading: false, notFound: false });
      return;
    }
    let cancelled = false;
    setState(s => ({ ...s, loading: true }));

    const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";
    fetch(`${base}${path}`, { headers: { Accept: "application/json" } })
      .then(async res => {
        if (cancelled) return;
        // 204 No Content and 404 Not Found are both "nothing to show" from
        // the UI's perspective — surface as notFound so callers can hide
        // the tab/section uniformly.
        if (res.status === 404 || res.status === 204) {
          setState({ data: null, error: null, loading: false, notFound: true });
          return;
        }
        if (!res.ok) {
          setState({
            data: null,
            error: new Error(`${res.status} ${res.statusText}: ${path}`),
            loading: false,
            notFound: false,
          });
          return;
        }
        const data = (await res.json()) as T;
        setState({ data, error: null, loading: false, notFound: false });
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setState({ data: null, error: err, loading: false, notFound: false });
      });

    return () => { cancelled = true; };
  }, [path]);

  return state;
}

// ── Typed sub-resources ──────────────────────────────────────

export interface PoliticianCore {
  id: string;
  name: string;
  first_name: string | null;
  last_name: string | null;
  party: string | null;
  elected_office: string | null;
  level: "federal" | "provincial" | "municipal";
  province_territory: string | null;
  constituency_name: string | null;
  constituency_id: string | null;
  email: string | null;
  photo_url: string | null;
  personal_url: string | null;
  official_url: string | null;
  social_urls: Record<string, string> | null;
  is_active: boolean;
  latest_term_ended_at: string | null;
}

export interface PoliticianDetailResponse {
  politician: PoliticianCore;
  websites: Array<{
    id: string;
    url: string;
    hostname: string;
    label: string | null;
    sovereignty_tier: number | null;
    hosting_provider: string | null;
    hosting_country: string | null;
  }>;
  boundary: unknown | null;
}

export interface PoliticianTerm {
  id: string;
  politician_id: string;
  office: string | null;
  party: string | null;
  level: string | null;
  province_territory: string | null;
  constituency_id: string | null;
  started_at: string;
  ended_at: string | null;
  source: string | null;
}

export interface PoliticianOffice {
  id: string;
  politician_id: string;
  kind: string | null;            // "constituency" | "legislature" | etc.
  address: string | null;
  city: string | null;
  province_territory: string | null;
  postal_code: string | null;
  phone: string | null;
  fax: string | null;
  email: string | null;
  hours: string | null;
  lat: number | null;
  lon: number | null;
  source: string | null;
}

export interface PoliticianCommittee {
  id: string;
  politician_id: string;
  name: string;
  role: string | null;
  level: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface PoliticianSocial {
  id: string;
  politician_id: string;
  platform: string;
  handle: string | null;
  url: string;
  last_verified_at: string | null;
  is_live: boolean | null;
  follower_count: number | null;
}

export interface PoliticianChange {
  id: string;
  politician_id: string;
  change_type: string;
  detected_at: string;
  // API returns strings for simple changes (email, personal_url) and
  // structured objects for others (social_added returns {url, handle, platform}).
  old_value: unknown;
  new_value: unknown;
  severity?: string | null;
  summary?: string | null;
}

// ── Public hooks ─────────────────────────────────────────────

export function usePolitician(id: string | null): AsyncState<PoliticianDetailResponse> {
  return useTolerantFetch<PoliticianDetailResponse>(id ? `/politicians/${encodeURIComponent(id)}` : null);
}

export function usePoliticianTerms(id: string | null): AsyncState<PoliticianTerm[] | { items: PoliticianTerm[] }> {
  return useTolerantFetch<PoliticianTerm[] | { items: PoliticianTerm[] }>(
    id ? `/politicians/${encodeURIComponent(id)}/terms` : null
  );
}

export function usePoliticianOffices(id: string | null): AsyncState<PoliticianOffice[] | { items: PoliticianOffice[] }> {
  return useTolerantFetch<PoliticianOffice[] | { items: PoliticianOffice[] }>(
    id ? `/politicians/${encodeURIComponent(id)}/offices` : null
  );
}

export function usePoliticianCommittees(
  id: string | null
): AsyncState<PoliticianCommittee[] | { items: PoliticianCommittee[] }> {
  return useTolerantFetch<PoliticianCommittee[] | { items: PoliticianCommittee[] }>(
    id ? `/politicians/${encodeURIComponent(id)}/committees` : null
  );
}

export function usePoliticianSocials(
  id: string | null
): AsyncState<PoliticianSocial[] | { items: PoliticianSocial[] }> {
  return useTolerantFetch<PoliticianSocial[] | { items: PoliticianSocial[] }>(
    id ? `/socials/politicians/${encodeURIComponent(id)}` : null
  );
}

export function usePoliticianChanges(
  id: string | null
): AsyncState<PoliticianChange[] | { items: PoliticianChange[] }> {
  // The existing /changes endpoint takes an owner_type/owner_id filter.
  // Also try a dedicated politician_changes endpoint that Phase 6 may land.
  return useTolerantFetch<PoliticianChange[] | { items: PoliticianChange[] }>(
    id ? `/politicians/${encodeURIComponent(id)}/changes` : null
  );
}

// ── openparliament.ca envelope ─────────────────────────────────────
export interface OpenparliamentEnvelope {
  source: "cache" | "fresh" | "stale";
  fetched_at: string;
  expires_at: string;
  warning?: string;
  data: {
    name?: string;
    url?: string;
    image?: string | null;
    email?: string | null;
    given_name?: string;
    family_name?: string;
    memberships?: Array<{
      url?: string;
      label?: Record<string, string>;
      party?: {
        name?: Record<string, string>;
        short_name?: Record<string, string>;
      };
      riding?: {
        id?: number;
        name?: Record<string, string>;
        province?: string;
      };
      start_date?: string;
      end_date?: string | null;
    }>;
    links?: Array<{ url: string; note?: string }>;
    other_info?: Record<string, unknown>;
    related?: {
      speeches_url?: string;
      ballots_url?: string;
      sponsored_bills_url?: string;
      activity_rss_url?: string;
    };
    [k: string]: unknown;
  };
}

/** Fetches openparliament.ca enrichment for a federal MP. Returns
 *  `notFound: true` when the politician is non-federal or the API responded
 *  with 204 (no slug known yet) — the Parliament tab stays hidden in those
 *  cases. */
export function usePoliticianOpenparliament(
  id: string | null
): AsyncState<OpenparliamentEnvelope> {
  // 204 responses come through as `data: null, notFound: false`. We treat
  // that identically to notFound so callers can hide the tab uniformly.
  const state = useTolerantFetch<OpenparliamentEnvelope>(
    id ? `/politicians/${encodeURIComponent(id)}/openparliament` : null
  );
  return state;
}

// ── openparliament activity (speeches + bills) ─────────────────────
export interface OpenparliamentSpeech {
  time: string;
  attribution?: Record<string, string>;
  content?: Record<string, string>;
  url?: string;
  h1?: Record<string, string>;
  h2?: Record<string, string>;
  procedural?: boolean;
  document_url?: string;
}

export interface OpenparliamentBill {
  session?: string;
  introduced?: string;
  name?: Record<string, string>;
  number?: string;
  url?: string;
}

export interface OpenparliamentActivityEnvelope {
  source: "cache" | "fresh" | "stale";
  fetched_at: string | null;
  expires_at: string | null;
  warning?: string;
  data: {
    speeches: OpenparliamentSpeech[];
    bills: OpenparliamentBill[];
  };
}

export function usePoliticianParliamentActivity(
  id: string | null
): AsyncState<OpenparliamentActivityEnvelope> {
  return useTolerantFetch<OpenparliamentActivityEnvelope>(
    id ? `/politicians/${encodeURIComponent(id)}/parliament-activity` : null
  );
}

/** Utility: extract a list from a variety of envelope shapes the API returns.
 *  - Plain arrays: `T[]`
 *  - Generic envelope: `{items: T[]}`
 *  - Resource-specific envelopes: `{politician, offices: T[]}`, `{politician, terms: T[]}`, etc.
 *
 *  Lets components stay agnostic to whichever envelope each endpoint uses
 *  (they aren't uniform across politicians.ts). */
export function itemsOf<T>(data: unknown): T[] {
  if (!data) return [];
  if (Array.isArray(data)) return data as T[];
  if (typeof data !== "object") return [];
  const obj = data as Record<string, unknown>;
  // Try common collection keys in order of specificity.
  for (const key of ["items", "offices", "terms", "committees", "socials", "changes"]) {
    const v = obj[key];
    if (Array.isArray(v)) return v as T[];
  }
  return [];
}
