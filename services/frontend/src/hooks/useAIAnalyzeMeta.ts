import { useFetch } from "./useFetch";

export interface AIAnalyzeMeta {
  enabled: boolean;
  model: string | null;
  provider: "openrouter";
}

/**
 * Reads /api/v1/contradictions/meta once per page load (cached in the
 * useFetch module-scope Map) so the whole search page can render N
 * result cards without making N meta calls. The shape drives two UI
 * decisions:
 *   - whether to show the "Analyze for contradictions (AI)" button at
 *     all (`enabled === false` means OPENROUTER_API_KEY is unset
 *     server-side → button greys out);
 *   - which model identifier to display in the consent modal, and key
 *     the localStorage consent record against.
 */
export function useAIAnalyzeMeta() {
  const { data, loading, error } = useFetch<AIAnalyzeMeta>("/contradictions/meta");
  return {
    meta: data,
    loading,
    error,
  };
}
