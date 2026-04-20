import { useCallback, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { userFetch, type SavedSearch } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";

/**
 * Follow/Unfollow a politician. UI-only over the existing saved-searches
 * stack — nothing politician-specific on the backend. A "follow" is just
 * a saved_search row with `filter_payload = { politician_id }` (no q)
 * and `alert_cadence = "daily"`; the alerts worker's filter-only path
 * handles it (no semantic embedding required).
 *
 * Detecting "already following" is a linear scan over the user's saved
 * searches; at phase-2 scale that's a few rows per user, so cheaper than
 * adding a dedicated endpoint.
 */

interface Props {
  politicianId: string;
  politicianName: string;
}

type FollowState =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "not_following" }
  | { kind: "following"; savedSearchId: string };

function isFollowOf(s: SavedSearch, politicianId: string): boolean {
  return (
    s.filter_payload?.politician_id === politicianId &&
    !(s.filter_payload?.q && s.filter_payload.q.trim())
  );
}

export function FollowPoliticianButton({ politicianId, politicianName }: Props) {
  const { user, loading: authLoading, disabled } = useUserAuth();
  const location = useLocation();
  const [state, setState] = useState<FollowState>({ kind: "loading" });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!user) {
      setState({ kind: "anonymous" });
      return;
    }
    try {
      const res = await userFetch<{ saved_searches: SavedSearch[] }>("/me/saved-searches");
      const match = res.saved_searches.find(s => isFollowOf(s, politicianId));
      setState(match
        ? { kind: "following", savedSearchId: match.id }
        : { kind: "not_following" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to check follow status.");
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
      <Link to={`/login?from=${from}`} className="cpd-follow cpd-follow--anon">
        Sign in to follow
      </Link>
    );
  }

  async function onClick() {
    setPending(true);
    setError(null);
    try {
      if (state.kind === "not_following") {
        await userFetch<SavedSearch>("/me/saved-searches", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: `Follow ${politicianName}`,
            filter_payload: { q: "", lang: "any", politician_id: politicianId },
            alert_cadence: "daily",
          }),
        });
      } else if (state.kind === "following") {
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
          state.kind === "following"
            ? "cpd-follow cpd-follow--on"
            : "cpd-follow"
        }
        onClick={onClick}
        disabled={pending}
        aria-pressed={state.kind === "following"}
      >
        {state.kind === "following"
          ? (pending ? "Unfollowing…" : "✓ Following · Unfollow")
          : (pending ? "Following…" : "Follow for daily alerts")}
      </button>
      {error && <span className="cpd-follow__error" role="alert">{error}</span>}
    </>
  );
}
