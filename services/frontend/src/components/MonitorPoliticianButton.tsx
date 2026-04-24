import { useCallback, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { userFetch, type SavedSearch } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";

/**
 * "Monitor politician" toggle on the politician profile page. UI-only
 * over the existing saved-searches stack — today a monitor is just a
 * saved_search row with `filter_payload = { politician_id }` (no q)
 * and `alert_cadence = "daily"`. The alerts worker's filter-only path
 * already handles it (no semantic embedding required).
 *
 * The "monitor" framing is intentionally broader than "follow": the
 * plan is to grow this into a multi-channel subscription that picks up
 * socials handle changes and website / hosting changes in addition to
 * Hansard speeches. When that lands it'll likely need either a
 * `subscription_type` column on saved_searches or a parallel
 * `politician_subscriptions` table; the button semantics stay the same.
 *
 * Detecting "already monitoring" is a linear scan over the user's
 * saved searches; at current scale that's a few rows per user, cheaper
 * than a dedicated endpoint.
 */

interface Props {
  politicianId: string;
  politicianName: string;
}

type MonitorState =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "off" }
  | { kind: "on"; savedSearchId: string };

function isMonitorOf(s: SavedSearch, politicianId: string): boolean {
  const fp = s.filter_payload;
  if (!fp) return false;
  if (fp.q && fp.q.trim()) return false;
  // Match either the legacy singular pin or a single-element canonical
  // politician_ids array — both represent "monitor exactly this person".
  const ids = fp.politician_ids ?? [];
  const legacy = fp.politician_id;
  if (ids.length === 1 && ids[0] === politicianId && !legacy) return true;
  if (ids.length === 0 && legacy === politicianId) return true;
  return false;
}

export function MonitorPoliticianButton({ politicianId, politicianName }: Props) {
  const { user, loading: authLoading, disabled } = useUserAuth();
  const location = useLocation();
  const [state, setState] = useState<MonitorState>({ kind: "loading" });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!user) {
      setState({ kind: "anonymous" });
      return;
    }
    try {
      const res = await userFetch<{ saved_searches: SavedSearch[] }>("/me/saved-searches");
      const match = res.saved_searches.find(s => isMonitorOf(s, politicianId));
      setState(match
        ? { kind: "on", savedSearchId: match.id }
        : { kind: "off" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to check monitor status.");
    }
  }, [user, politicianId]);

  useEffect(() => {
    if (authLoading) {
      setState({ kind: "loading" });
      return;
    }
    void refresh();
  }, [authLoading, refresh]);

  if (disabled) return null;
  if (state.kind === "loading") return null;

  if (state.kind === "anonymous") {
    const from = encodeURIComponent(location.pathname + location.search);
    return (
      <Link to={`/login?from=${from}`} className="cpd-monitor cpd-monitor--anon">
        Sign in to monitor
      </Link>
    );
  }

  async function onClick() {
    setPending(true);
    setError(null);
    try {
      if (state.kind === "off") {
        await userFetch<SavedSearch>("/me/saved-searches", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: `Monitor ${politicianName}`,
            filter_payload: { q: "", lang: "any", politician_ids: [politicianId] },
            alert_cadence: "daily",
          }),
        });
      } else if (state.kind === "on") {
        await userFetch<void>(`/me/saved-searches/${state.savedSearchId}`, {
          method: "DELETE",
        });
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed.");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={
          state.kind === "on"
            ? "cpd-monitor cpd-monitor--on"
            : "cpd-monitor"
        }
        onClick={onClick}
        disabled={pending}
        aria-pressed={state.kind === "on"}
      >
        {state.kind === "on"
          ? (pending ? "Stopping…" : "✓ Monitoring · Stop")
          : (pending ? "Starting…" : "Monitor · daily alerts")}
      </button>
      {error && <span className="cpd-monitor__error" role="alert">{error}</span>}
    </>
  );
}
