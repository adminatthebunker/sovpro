import { useEffect, useState } from "react";

export interface SpeechSearchSocial {
  platform: string;
  url: string;
  handle: string | null;
}

export interface SpeechSearchPolitician {
  id: string;
  name: string | null;
  slug: string | null;
  photo_url: string | null;
  party: string | null;
  socials: SpeechSearchSocial[];
}

export interface SpeechSearchSession {
  parliament_number: number;
  session_number: number;
}

export interface SpeechSearchItem {
  chunk_id: string;
  speech_id: string;
  chunk_index: number;
  text: string;
  snippet_html: string | null;
  similarity: number | null;
  spoken_at: string | null;
  language: "en" | "fr";
  level: string | null;
  province_territory: string | null;
  party_at_time: string | null;
  politician: SpeechSearchPolitician | null;
  speech: {
    speaker_name_raw: string;
    source_url: string | null;
    source_anchor: string | null;
    session: SpeechSearchSession | null;
  };
}

export interface SpeechSearchResponse {
  items: SpeechSearchItem[];
  page: number;
  limit: number;
  total: number;
  totalCapped: boolean;
  pages: number;
  mode: "semantic" | "recent";
}

export interface SpeechSearchFilter {
  q?: string;
  lang?: "en" | "fr" | "any";
  level?: "federal" | "provincial" | "municipal";
  province_territory?: string;
  politician_id?: string;
  party?: string;
  from?: string;
  to?: string;
  page?: number;
  limit?: number;
}

export interface SpeechSearchMeta {
  total_chunks: number;
  embedded_chunks: number;
  coverage: number;
}

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

export function buildSpeechSearchQuery(f: SpeechSearchFilter): string {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.lang && f.lang !== "any") p.set("lang", f.lang);
  if (f.level) p.set("level", f.level);
  if (f.province_territory) p.set("province_territory", f.province_territory);
  if (f.politician_id) p.set("politician_id", f.politician_id);
  if (f.party) p.set("party", f.party);
  if (f.from) p.set("from", f.from);
  if (f.to) p.set("to", f.to);
  p.set("page", String(f.page ?? 1));
  p.set("limit", String(f.limit ?? 20));
  return p.toString();
}

// `enabled=false` keeps the hook inert until the caller decides — useful on
// /search where we want the empty landing state until the user types.
export function useSpeechSearch(filter: SpeechSearchFilter, enabled = true): AsyncState<SpeechSearchResponse> {
  const [state, setState] = useState<AsyncState<SpeechSearchResponse>>({
    data: null,
    error: null,
    loading: enabled,
  });

  const qs = buildSpeechSearchQuery(filter);

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));

    const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";
    fetch(`${base}/search/speeches?${qs}`, { headers: { Accept: "application/json" } })
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) {
          const body = await res.text().catch(() => "");
          setState({
            data: null,
            error: new Error(`${res.status} ${res.statusText}${body ? `: ${body}` : ""}`),
            loading: false,
          });
          return;
        }
        const data = (await res.json()) as SpeechSearchResponse;
        setState({ data, error: null, loading: false });
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setState({ data: null, error: err, loading: false });
      });

    return () => {
      cancelled = true;
    };
  }, [qs, enabled]);

  return state;
}

export function useSpeechSearchMeta(): AsyncState<SpeechSearchMeta> {
  const [state, setState] = useState<AsyncState<SpeechSearchMeta>>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";
    fetch(`${base}/search/meta`, { headers: { Accept: "application/json" } })
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) {
          setState({ data: null, error: new Error(`${res.status} ${res.statusText}`), loading: false });
          return;
        }
        const data = (await res.json()) as SpeechSearchMeta;
        setState({ data, error: null, loading: false });
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setState({ data: null, error: err, loading: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
