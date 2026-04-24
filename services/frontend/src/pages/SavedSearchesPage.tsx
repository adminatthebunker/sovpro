import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { userFetch, type SavedSearch } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { buildSpeechSearchQuery, type SpeechSearchFilter } from "../hooks/useSpeechSearch";
import { SpeechFilters } from "../components/SpeechFilters";

type FilterPayload = SavedSearch["filter_payload"];

interface EditDraft {
  name: string;
  filter: FilterPayload;
  cadence: SavedSearch["alert_cadence"];
}

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
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

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

  function onStartEdit(s: SavedSearch) {
    setEditingId(s.id);
    setDraft({
      name: s.name,
      filter: { ...s.filter_payload },
      cadence: s.alert_cadence,
    });
    setError(null);
  }

  function onCancelEdit() {
    setEditingId(null);
    setDraft(null);
  }

  function onDraftFilterChange(patch: Partial<SpeechSearchFilter>) {
    setDraft(d => (d ? { ...d, filter: { ...d.filter, ...patch } as FilterPayload } : d));
  }

  function onClearPoliticianPin() {
    setDraft(d => {
      if (!d) return d;
      const {
        politician_id: _legacy,
        politician_ids: _ids,
        ...rest
      } = d.filter;
      return { ...d, filter: rest };
    });
  }

  async function onSaveEdit(e: FormEvent) {
    e.preventDefault();
    if (!draft || !editingId) return;
    setSavingEdit(true);
    setError(null);
    try {
      // Strip empty strings so the zod baseFilterSchema happily accepts
      // them as "unset" rather than rejecting length-0 province codes etc.
      const f = draft.filter;
      const payload: Record<string, unknown> = {
        q: (f.q ?? "").trim(),
        lang: f.lang ?? "any",
      };
      if (f.level) payload.level = f.level;
      if (f.province_territory) payload.province_territory = f.province_territory;
      // Canonicalize on write: fold any surviving legacy politician_id
      // into politician_ids, never send both.
      const pids = f.politician_ids && f.politician_ids.length > 0
        ? f.politician_ids
        : (f.politician_id ? [f.politician_id] : []);
      if (pids.length > 0) payload.politician_ids = pids;
      if (f.party && f.party.trim()) payload.party = f.party.trim();
      if (f.from) payload.from = f.from;
      if (f.to) payload.to = f.to;

      const updated = await userFetch<SavedSearch>(`/me/saved-searches/${editingId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: draft.name.trim(),
          filter_payload: payload,
          alert_cadence: draft.cadence,
        }),
      });
      setItems(prev => prev?.map(x => (x.id === updated.id ? updated : x)) ?? null);
      setEditingId(null);
      setDraft(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed.");
    } finally {
      setSavingEdit(false);
    }
  }

  return (
    <section className="cpd-auth cpd-auth--account">
      <p className="cpd-auth__backlink">
        <Link to="/account" className="cpd-auth__linklike">← Back to account</Link>
      </p>
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
          {items.map(s => {
            const isEditing = editingId === s.id;
            return (
              <li key={s.id} className="cpd-saved-search">
                <div className="cpd-saved-search__head">
                  <strong>{s.name}</strong>
                  <span className="cpd-saved-search__meta">
                    {s.filter_payload.q && <code>"{s.filter_payload.q}"</code>}
                    {s.filter_payload.lang && s.filter_payload.lang !== "any" && (
                      <span> · {s.filter_payload.lang}</span>
                    )}
                    {s.filter_payload.level && <span> · {s.filter_payload.level}</span>}
                    {s.filter_payload.province_territory && (
                      <span> · {s.filter_payload.province_territory}</span>
                    )}
                    {s.filter_payload.party && <span> · {s.filter_payload.party}</span>}
                    {(() => {
                      const pids = s.filter_payload.politician_ids ?? [];
                      const legacy = s.filter_payload.politician_id;
                      const n = pids.length || (legacy ? 1 : 0);
                      if (n === 0) return null;
                      return (
                        <span className="cpd-saved-search__pin">
                          {" · "}pinned to {n === 1 ? "1 politician" : `${n} politicians`}
                        </span>
                      );
                    })()}
                    {s.filter_payload.from && <span> · from {s.filter_payload.from}</span>}
                    {s.filter_payload.to && <span> · to {s.filter_payload.to}</span>}
                  </span>
                </div>
                {!isEditing && (
                  <div className="cpd-saved-search__actions">
                    <button type="button" onClick={() => onRerun(s)}>Run now</button>
                    <button type="button" onClick={() => onStartEdit(s)}>Edit</button>
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
                )}
                {isEditing && draft && (
                  <form className="cpd-saved-search__edit" onSubmit={onSaveEdit}>
                    <label className="cpd-saved-search__edit-field">
                      <span>Name</span>
                      <input
                        type="text"
                        value={draft.name}
                        onChange={e => setDraft(d => (d ? { ...d, name: e.target.value } : d))}
                        maxLength={100}
                        required
                      />
                    </label>
                    <label className="cpd-saved-search__edit-field">
                      <span>Search text</span>
                      <input
                        type="text"
                        value={draft.filter.q ?? ""}
                        onChange={e =>
                          setDraft(d =>
                            d ? { ...d, filter: { ...d.filter, q: e.target.value } } : d
                          )
                        }
                        maxLength={500}
                        placeholder="Leave empty for a filter-only alert"
                      />
                    </label>
                    <SpeechFilters
                      value={draft.filter as SpeechSearchFilter}
                      onChange={onDraftFilterChange}
                    />
                    {(() => {
                      const pids = draft.filter.politician_ids ?? [];
                      const legacy = draft.filter.politician_id;
                      const n = pids.length || (legacy ? 1 : 0);
                      if (n === 0) return null;
                      return (
                        <p className="cpd-saved-search__pin-row">
                          <span>
                            {n === 1
                              ? "Pinned to 1 politician."
                              : `Pinned to ${n} politicians.`}
                          </span>{" "}
                          <button
                            type="button"
                            className="cpd-auth__linkbtn"
                            onClick={onClearPoliticianPin}
                          >
                            Clear {n === 1 ? "pin" : "pins"}
                          </button>
                        </p>
                      );
                    })()}
                    <label className="cpd-saved-search__edit-field">
                      <span>Alerts</span>
                      <select
                        value={draft.cadence}
                        onChange={e =>
                          setDraft(d =>
                            d
                              ? { ...d, cadence: e.target.value as SavedSearch["alert_cadence"] }
                              : d
                          )
                        }
                      >
                        <option value="none">Off — just save it</option>
                        <option value="daily">Daily digest</option>
                        <option value="weekly">Weekly digest</option>
                      </select>
                    </label>
                    <div className="cpd-saved-search__actions">
                      <button type="submit" disabled={savingEdit || !draft.name.trim()}>
                        {savingEdit ? "Saving…" : "Save changes"}
                      </button>
                      <button
                        type="button"
                        className="cpd-auth__linkbtn"
                        onClick={onCancelEdit}
                        disabled={savingEdit}
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                )}
                {!isEditing && s.alert_cadence !== "none" && !s.has_embedding && (
                  <p className="cpd-saved-search__warn">
                    This search has no query text; alerts will use filters only.
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
