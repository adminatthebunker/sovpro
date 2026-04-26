import { useFetch } from "./useFetch";

export interface LegislativeSession {
  parliament_number: number;
  session_number: number;
  name: string | null;
  start_date: string | null;
  end_date: string | null;
}

interface SessionsResponse {
  sessions: LegislativeSession[];
}

/**
 * List of (parliament, session) pairs for a given jurisdiction. Powers the
 * cascading dropdown in the Hansard search Advanced filters. Results are
 * cached at the module scope by useFetch keyed on the full path string,
 * so toggling province in the filter UI never re-fetches a province
 * already loaded this session.
 *
 * Pass `null`/`undefined` for `level` to disable the fetch (e.g. while
 * the user hasn't picked a level yet).
 */
export function useLegislativeSessions(
  level: "federal" | "provincial" | "municipal" | undefined,
  province: string | undefined,
) {
  const path = (() => {
    if (!level) return null;
    const p = new URLSearchParams();
    p.set("level", level);
    if (province) p.set("province", province);
    return `/search/sessions?${p.toString()}`;
  })();
  const { data, loading, error } = useFetch<SessionsResponse>(path);
  return {
    sessions: data?.sessions ?? [],
    loading,
    error,
  };
}
