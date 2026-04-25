import { useEffect, useState } from "react";
import { userFetch } from "../api";

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
    speaker_role: string | null;
    source_url: string | null;
    source_anchor: string | null;
    session: SpeechSearchSession | null;
  };
}

export interface TimelineSearchResponse {
  mode: "semantic" | "recent";
  items: SpeechSearchItem[];
  page: number;
  limit: number;
  total: number;
  totalCapped: boolean;
  pages: number;
}

/** A chunk inside a PoliticianGroup — identical to SpeechSearchItem but
 *  with the `politician` hoisted to the group header, so we omit it here. */
export interface GroupedSearchChunk {
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
  speech: SpeechSearchItem["speech"];
}

export type PoliticianSort = "mentions" | "best_match" | "avg_match" | "keyword_hits";

export interface PoliticianSearchGroup {
  politician: SpeechSearchPolitician;
  best_similarity: number | null;
  avg_similarity: number | null;
  mention_count: number;
  keyword_hits: number;
  chunks: GroupedSearchChunk[];
}

export interface GroupedSearchResponse {
  mode: "grouped";
  group_by: "politician";
  page: number;
  limit: number;
  per_group_limit: number;
  groups: PoliticianSearchGroup[];
  total_politicians: number;
}

export type SpeechSearchResponse = TimelineSearchResponse | GroupedSearchResponse;

export interface SpeechSearchFilter {
  q?: string;
  lang?: "en" | "fr" | "any";
  level?: "federal" | "provincial" | "municipal";
  province_territory?: string;
  /** Legacy singular pin. New code should prefer `politician_ids`; both
   *  are accepted by the API for back-compat. */
  politician_id?: string;
  /** Canonical multi-select politician pin, cap 10 at the API. */
  politician_ids?: string[];
  party?: string;
  from?: string;
  to?: string;
  /** Drop speeches uttered in a presiding role (Speaker, Chair, Président)
   *  from results. The chair-speech corpus is dominated by procedural
   *  filler ("I declare the motion lost"), which floods semantic search
   *  for unrelated phrases. */
  exclude_presiding?: boolean;
  page?: number;
  limit?: number;
  group_by?: "timeline" | "politician";
  per_group_limit?: number;
  // Only meaningful when group_by === "politician".
  sort?: PoliticianSort;
}

/** Collapse the legacy singular + canonical array into a single list. */
export function effectivePoliticianIds(f: SpeechSearchFilter): string[] {
  if (f.politician_ids && f.politician_ids.length > 0) return f.politician_ids;
  if (f.politician_id) return [f.politician_id];
  return [];
}

export const MAX_POLITICIAN_PINS = 10;

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
  // URL convention: repeated `politician_id=uuid` params (standard
  // URLSearchParams multi-value handling). Backward compatible with
  // single-pin URLs shared/bookmarked before Phase 2.
  for (const id of effectivePoliticianIds(f)) p.append("politician_id", id);
  if (f.party) p.set("party", f.party);
  if (f.from) p.set("from", f.from);
  if (f.to) p.set("to", f.to);
  if (f.exclude_presiding) p.set("exclude_presiding", "true");
  p.set("page", String(f.page ?? 1));
  p.set("limit", String(f.limit ?? 20));
  if (f.group_by && f.group_by !== "timeline") p.set("group_by", f.group_by);
  if (f.per_group_limit) p.set("per_group_limit", String(f.per_group_limit));
  if (f.group_by === "politician" && f.sort) p.set("sort", f.sort);
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

/** Fetch every chunk a single politician has matching the parent search's
 *  query + filters. Backs the "Show all N matching quotes" expand
 *  affordance on /search's politician view. Hits the gated
 *  /search/politician-quotes endpoint, which requires a signed-in
 *  session — anon callers get UserUnauthorizedError before this resolves.
 *
 *  Returns the same TimelineSearchResponse shape as a regular timeline
 *  search so the caller can append items to its existing chunk list
 *  without an adapter. */
export async function fetchPoliticianQuotes(
  politicianId: string,
  parentFilter: SpeechSearchFilter,
  page: number,
  options: { limit?: number; minSimilarity?: number } = {},
): Promise<TimelineSearchResponse> {
  const { limit = 50, minSimilarity } = options;
  const p = new URLSearchParams();
  p.set("politician_id", politicianId);
  if (parentFilter.q) p.set("q", parentFilter.q);
  if (parentFilter.lang && parentFilter.lang !== "any") p.set("lang", parentFilter.lang);
  if (parentFilter.level) p.set("level", parentFilter.level);
  if (parentFilter.province_territory) p.set("province_territory", parentFilter.province_territory);
  if (parentFilter.party) p.set("party", parentFilter.party);
  if (parentFilter.from) p.set("from", parentFilter.from);
  if (parentFilter.to) p.set("to", parentFilter.to);
  if (parentFilter.exclude_presiding) p.set("exclude_presiding", "true");
  // Server clamps to >= 0.45; sending a value above that tightens the
  // floor for this request so paging reshuffles to match. Omitted when
  // the UI is at "All matches" (default 0), to keep URLs minimal.
  if (minSimilarity != null && minSimilarity > 0) {
    p.set("min_similarity", String(minSimilarity));
  }
  p.set("page", String(page));
  p.set("limit", String(limit));
  return userFetch<TimelineSearchResponse>(`/search/politician-quotes?${p.toString()}`);
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
