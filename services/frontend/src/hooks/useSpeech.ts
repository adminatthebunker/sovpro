import { useEffect, useState } from "react";
import type { SpeechSearchPolitician, SpeechSearchSession } from "./useSpeechSearch";

export interface SpeechDetail {
  id: string;
  session_id: string;
  level: string;
  province_territory: string | null;
  speaker_name_raw: string;
  speaker_role: string | null;
  party_at_time: string | null;
  constituency_at_time: string | null;
  speech_type: string | null;
  spoken_at: string | null;
  sequence: number | null;
  language: string;
  text: string;
  word_count: number | null;
  source_system: string;
  source_url: string;
  source_anchor: string | null;
  politician: SpeechSearchPolitician | null;
  session: SpeechSearchSession | null;
}

export interface SpeechChunkSummary {
  id: string;
  chunk_index: number;
  text: string;
  char_start: number;
  char_end: number;
  language: string;
}

export interface SpeechDetailResponse {
  speech: SpeechDetail;
  chunks: SpeechChunkSummary[];
}

export interface SpeechDetailState {
  data: SpeechDetailResponse | null;
  error: Error | null;
  loading: boolean;
  notFound: boolean;
}

export function useSpeech(id: string | null): SpeechDetailState {
  const [state, setState] = useState<SpeechDetailState>({
    data: null,
    error: null,
    loading: !!id,
    notFound: false,
  });

  useEffect(() => {
    if (!id) {
      setState({ data: null, error: null, loading: false, notFound: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null, notFound: false }));

    const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";
    fetch(`${base}/speeches/${encodeURIComponent(id)}`, { headers: { Accept: "application/json" } })
      .then(async (res) => {
        if (cancelled) return;
        if (res.status === 404) {
          setState({ data: null, error: null, loading: false, notFound: true });
          return;
        }
        if (!res.ok) {
          setState({
            data: null,
            error: new Error(`${res.status} ${res.statusText}`),
            loading: false,
            notFound: false,
          });
          return;
        }
        const data = (await res.json()) as SpeechDetailResponse;
        setState({ data, error: null, loading: false, notFound: false });
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setState({ data: null, error: err, loading: false, notFound: false });
      });

    return () => {
      cancelled = true;
    };
  }, [id]);

  return state;
}
