import { useEffect, useState } from "react";
import type { PoliticianCore } from "./usePolitician";

export interface PoliticianListItem extends PoliticianCore {
  website_count: number;
  social_platforms: string[];
  office_count: number;
  committee_count: number;
  current_term_started_at: string | null;
  latest_speech_text: string | null;
  latest_speech_at: string | null;
}

export interface PoliticianListResponse {
  items: PoliticianListItem[];
  page: number;
  limit: number;
  total: number;
  pages: number;
}

export interface PoliticiansFilter {
  level?: "federal" | "provincial" | "municipal";
  province?: string;
  party?: string;
  search?: string;
  has_twitter?: boolean;
  has_facebook?: boolean;
  has_instagram?: boolean;
  socials_live?: boolean;
  page?: number;
  limit?: number;
}

export interface AsyncListState {
  data: PoliticianListResponse | null;
  error: Error | null;
  loading: boolean;
}

/** Build the query string for /api/v1/politicians from the filter object.
 *  Kept outside the hook so <PoliticiansPage> can render it for the mini-map
 *  URL too (keeping the map in sync with the card filter). */
export function buildPoliticiansQuery(f: PoliticiansFilter): string {
  const params = new URLSearchParams();
  if (f.level) params.set("level", f.level);
  if (f.province) params.set("province", f.province);
  if (f.party) params.set("party", f.party);
  if (f.search) params.set("search", f.search);
  if (f.has_twitter) params.set("has_twitter", "true");
  if (f.has_facebook) params.set("has_facebook", "true");
  if (f.has_instagram) params.set("has_instagram", "true");
  if (f.socials_live) params.set("socials_live", "true");
  params.set("page", String(f.page ?? 1));
  params.set("limit", String(f.limit ?? 40));
  return params.toString();
}

export function usePoliticians(filter: PoliticiansFilter): AsyncListState {
  const qs = buildPoliticiansQuery(filter);
  const [state, setState] = useState<AsyncListState>({ data: null, error: null, loading: true });

  useEffect(() => {
    let cancelled = false;
    setState(s => ({ ...s, loading: true }));

    const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";
    fetch(`${base}/politicians?${qs}`, { headers: { Accept: "application/json" } })
      .then(async res => {
        if (cancelled) return;
        if (!res.ok) {
          setState({ data: null, error: new Error(`${res.status} ${res.statusText}`), loading: false });
          return;
        }
        const data = (await res.json()) as PoliticianListResponse;
        setState({ data, error: null, loading: false });
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setState({ data: null, error: err, loading: false });
      });

    return () => { cancelled = true; };
  }, [qs]);

  return state;
}
