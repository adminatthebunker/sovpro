import { useEffect, useState } from "react";
import { buildSpeechSearchQuery, type SpeechSearchFilter, type AsyncState } from "./useSpeechSearch";

export interface FacetPolitician {
  id: string;
  name: string | null;
  slug: string | null;
}

export interface FacetParty {
  party: string | null;
  count: number;
  avg_similarity: number | null;
}

export interface FacetPoliticianRow {
  politician: FacetPolitician | null;
  count: number;
  avg_similarity: number | null;
}

export interface FacetYear {
  year: number;
  count: number;
}

export interface FacetLanguage {
  language: "en" | "fr";
  count: number;
}

export interface FacetKeywordOverlap {
  both: number;
  semantic_only: number;
}

export interface FacetsResponse {
  analyzed_count: number;
  analysis_limit: number;
  by_party: FacetParty[];
  by_politician: FacetPoliticianRow[];
  by_year: FacetYear[];
  by_language: FacetLanguage[];
  keyword_overlap: FacetKeywordOverlap | null;
  mode: "semantic" | "recent";
}

export function useSpeechFacets(filter: SpeechSearchFilter, enabled = true): AsyncState<FacetsResponse> {
  const [state, setState] = useState<AsyncState<FacetsResponse>>({
    data: null,
    error: null,
    loading: enabled,
  });

  // Strip page/limit from the filter — facets aren't paginated. Reusing
  // buildSpeechSearchQuery and then stripping keeps the shared filter
  // surface consistent.
  const qs = buildSpeechSearchQuery({ ...filter, page: undefined, limit: undefined });

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));

    const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";
    fetch(`${base}/search/facets?${qs}`, { headers: { Accept: "application/json" } })
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
        const data = (await res.json()) as FacetsResponse;
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
