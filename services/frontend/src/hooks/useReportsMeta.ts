import { useEffect, useState } from "react";
import { fetchJson, type ReportsMeta } from "../api";

/**
 * Fetches `/reports/meta` once per page load. Mirrors useAIAnalyzeMeta.
 *
 * The button consuming this should treat `loading` and `!enabled` as
 * the same disabled state — there's nothing the user can do until the
 * server reports a configured model.
 */
export function useReportsMeta(): {
  meta: ReportsMeta | null;
  loading: boolean;
  error: string | null;
} {
  const [meta, setMeta] = useState<ReportsMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const m = await fetchJson<ReportsMeta>("/reports/meta");
        if (!cancelled) setMeta(m);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "unknown");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { meta, loading, error };
}
