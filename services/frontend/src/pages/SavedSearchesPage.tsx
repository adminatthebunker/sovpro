import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { userFetch, type SavedSearch } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { buildSpeechSearchQuery } from "../hooks/useSpeechSearch";

/**
 * List / delete / re-run saved searches. Re-running is just a link back
 * to /search?… with the persisted filter payload URL-encoded via the
 * existing buildSpeechSearchQuery helper — no new search-state plumbing.
 * Toggling alert_cadence uses the same PATCH endpoint the future
 * SaveSearchButton will reuse.
 */
export default function SavedSearchesPage() {
  useDocumentTitle("Saved searches · Canadian Political Data");
  const { user, loading: authLoading, disabled } = useUserAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState<SavedSearch[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await userFetch<{ saved_searches: SavedSearch[] }>("/me/saved-searches");
      setItems(res.saved_searches);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Load failed.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user) void load();
  }, [user, load]);

  if (authLoading) return <section className="cpd-auth"><p>Loading…</p></section>;
  if (disabled) {
    return (
      <section className="cpd-auth">
        <h2>Accounts unavailable</h2>
        <p>User accounts are not configured on this server.</p>
      </section>
    );
  }
  if (!user) {
    return (
      <section className="cpd-auth">
        <h2>Sign in to see your saved searches</h2>
        <p><Link to="/login?from=/account/saved-searches">Sign in →</Link></p>
      </section>
    );
  }

  async function onToggleCadence(s: SavedSearch, cadence: SavedSearch["alert_cadence"]) {
    try {
      const updated = await userFetch<SavedSearch>(`/me/saved-searches/${s.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_cadence: cadence }),
      });
      setItems(prev => prev?.map(x => (x.id === s.id ? updated : x)) ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed.");
    }
  }

  async function onDelete(s: SavedSearch) {
    if (!confirm(`Delete "${s.name}"?`)) return;
    try {
      await userFetch<void>(`/me/saved-searches/${s.id}`, { method: "DELETE" });
      setItems(prev => prev?.filter(x => x.id !== s.id) ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    }
  }

  async function onCopyFeed(s: SavedSearch) {
    if (!s.feed_url) return;
    try {
      await navigator.clipboard.writeText(s.feed_url);
      setError(null);
      // lightweight toast via the same state slot the error uses isn't
      // great UX — but keep things simple for now; a dedicated toast
      // system is a later polish.
      setCopiedId(s.id);
      setTimeout(() => setCopiedId(curr => (curr === s.id ? null : curr)), 2000);
    } catch {
      setError("Copy failed — your browser may have blocked clipboard access.");
    }
  }

  function onRerun(s: SavedSearch) {
    const qs = buildSpeechSearchQuery({ ...s.filter_payload, page: 1, limit: 20 });
    navigate(`/search?${qs}`);
  }

  return (
    <section className="cpd-auth cpd-auth--account">
      <h2>Saved searches</h2>
      {loading && <p>Loading…</p>}
      {error && <p className="cpd-auth__error" role="alert">{error}</p>}
      {items && items.length === 0 && (
        <p className="cpd-auth__muted">
          You haven't saved any searches yet.{" "}
          <Link to="/search">Run a search</Link> and click "Save" to get started.
        </p>
      )}
      {items && items.length > 0 && (
        <ul className="cpd-saved-searches">
          {items.map(s => (
            <li key={s.id} className="cpd-saved-search">
              <div className="cpd-saved-search__head">
                <strong>{s.name}</strong>
                <span className="cpd-saved-search__meta">
                  {s.filter_payload.q && <code>"{s.filter_payload.q}"</code>}
                  {s.filter_payload.province_territory && <span> · {s.filter_payload.province_territory}</span>}
                  {s.filter_payload.level && <span> · {s.filter_payload.level}</span>}
                  {s.filter_payload.from && <span> · from {s.filter_payload.from}</span>}
                  {s.filter_payload.to && <span> · to {s.filter_payload.to}</span>}
                </span>
              </div>
              <div className="cpd-saved-search__actions">
                <button type="button" onClick={() => onRerun(s)}>Run now</button>
                <label>
                  <span>Alerts:</span>
                  <select
                    value={s.alert_cadence}
                    onChange={e => onToggleCadence(s, e.target.value as SavedSearch["alert_cadence"])}
                  >
                    <option value="none">off</option>
                    <option value="daily">daily</option>
                    <option value="weekly">weekly</option>
                  </select>
                </label>
                {s.feed_url && (
                  <button
                    type="button"
                    onClick={() => onCopyFeed(s)}
                    title="Copy a URL you can paste into any RSS reader"
                  >
                    {copiedId === s.id ? "✓ Copied" : "RSS"}
                  </button>
                )}
                <button
                  type="button"
                  className="cpd-auth__linkbtn"
                  onClick={() => onDelete(s)}
                >
                  Delete
                </button>
              </div>
              {s.alert_cadence !== "none" && !s.has_embedding && (
                <p className="cpd-saved-search__warn">
                  This search has no query text; alerts will use filters only.
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
