import { FormEvent, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  buildSpeechSearchQuery,
  type SpeechSearchFilter,
  type SpeechSearchItem,
  type TimelineSearchResponse,
} from "../hooks/useSpeechSearch";
import { useUserAuth } from "../hooks/useUserAuth";
import { fetchJson, userFetch, type SavedSearch } from "../api";

/**
 * "Save this search" affordance for the search page.
 *
 * Rendering matrix:
 *   - accounts disabled on server → render nothing
 *   - not signed in               → "Sign in to save" link
 *   - signed in                   → "Save this search" button that
 *                                     toggles an inline form (name +
 *                                     alert cadence) and POSTs to
 *                                     /me/saved-searches
 *
 * Intentionally an inline details-style form rather than a modal —
 * modals are heavier dependencies, and the search page already has
 * plenty of controls so adding another one is unsurprising.
 */

function isEmptyFilter(f: SpeechSearchFilter): boolean {
  return !(
    (f.q && f.q.trim()) ||
    f.level ||
    f.province_territory ||
    f.politician_id ||
    f.party ||
    f.from ||
    f.to
  );
}

interface Props {
  filter: SpeechSearchFilter;
}

export function SaveSearchButton({ filter }: Props) {
  const { user, disabled } = useUserAuth();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [cadence, setCadence] = useState<SavedSearch["alert_cadence"]>("daily");
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState<{ kind: "ok" | "err"; message: string } | null>(null);
  const [preview, setPreview] = useState<SpeechSearchItem[] | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  if (disabled) return null;
  if (isEmptyFilter(filter)) return null;

  if (!user) {
    const from = encodeURIComponent(location.pathname + location.search);
    return (
      <Link to={`/login?from=${from}`} className="cpd-save-search cpd-save-search--anon">
        Sign in to save this search
      </Link>
    );
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setStatus(null);
    try {
      // Strip page/limit/group_by — they're UI state, not part of the
      // saved search identity. filter_payload matches the server's
      // baseFilterSchema (no page/limit fields).
      const {
        q = "",
        lang = "any",
        level,
        province_territory,
        politician_id,
        party,
        from,
        to,
      } = filter;
      const payload: Record<string, unknown> = { q, lang };
      if (level) payload.level = level;
      if (province_territory) payload.province_territory = province_territory;
      if (politician_id) payload.politician_id = politician_id;
      if (party) payload.party = party;
      if (from) payload.from = from;
      if (to) payload.to = to;

      await userFetch<SavedSearch>("/me/saved-searches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          filter_payload: payload,
          alert_cadence: cadence,
        }),
      });
      setStatus({ kind: "ok", message: "Saved." });
      setName("");
      setOpen(false);
    } catch (e) {
      setStatus({
        kind: "err",
        message: e instanceof Error ? e.message : "Save failed.",
      });
    } finally {
      setSubmitting(false);
    }
  }

  const defaultName = (filter.q && filter.q.trim()) || "My saved search";

  async function onPreview() {
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const today = new Date();
      const d30 = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000);
      const fmt = (d: Date) => d.toISOString().slice(0, 10); // YYYY-MM-DD
      const qs = buildSpeechSearchQuery({
        ...filter,
        from: fmt(d30),
        to: fmt(today),
        page: 1,
        limit: 10,
      });
      const res = await fetchJson<TimelineSearchResponse>(`/search/speeches?${qs}`);
      setPreview(res.items ?? []);
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : "Preview failed.");
    } finally {
      setPreviewLoading(false);
    }
  }

  return (
    <div className="cpd-save-search">
      {!open ? (
        <button
          type="button"
          className="cpd-save-search__toggle"
          onClick={() => {
            setOpen(true);
            if (!name) setName(defaultName);
          }}
        >
          ＋ Save this search
        </button>
      ) : (
        <form className="cpd-save-search__form" onSubmit={onSubmit}>
          <label>
            <span>Name</span>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              maxLength={100}
              required
              autoFocus
            />
          </label>
          <label>
            <span>Alerts</span>
            <select value={cadence} onChange={e => setCadence(e.target.value as SavedSearch["alert_cadence"])}>
              <option value="none">Off — just save it</option>
              <option value="daily">Daily digest</option>
              <option value="weekly">Weekly digest</option>
            </select>
          </label>
          <div className="cpd-save-search__actions">
            <button type="submit" disabled={submitting || !name.trim()}>
              {submitting ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={onPreview}
              disabled={previewLoading}
              title="See what matches the last 30 days of Hansard for this search"
            >
              {previewLoading ? "Previewing…" : "Preview matches"}
            </button>
            <button
              type="button"
              className="cpd-auth__linkbtn"
              onClick={() => { setOpen(false); setStatus(null); setPreview(null); setPreviewError(null); }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
      {previewError && (
        <p className="cpd-auth__error" role="alert">{previewError}</p>
      )}
      {preview && (
        <div className="cpd-save-search__preview" aria-label="Preview of matches">
          <p className="cpd-save-search__preview-head">
            Last 30 days of matches
            {preview.length === 0 ? " — nothing yet." : ` (${preview.length}):`}
          </p>
          {preview.length > 0 && (
            <ul className="cpd-save-search__preview-list">
              {preview.map(m => {
                const date = m.spoken_at ? m.spoken_at.slice(0, 10) : "(no date)";
                const speaker = m.politician?.name || m.speech.speaker_name_raw || "Unknown";
                const snippet = (m.text || "").trim().slice(0, 220);
                return (
                  <li key={m.chunk_id}>
                    <span className="cpd-save-search__preview-meta">
                      <strong>{date}</strong> · {speaker}
                    </span>
                    <span className="cpd-save-search__preview-body">{snippet}{(m.text?.length ?? 0) > 220 ? "…" : ""}</span>
                    {m.speech.source_url && (
                      <a
                        href={m.speech.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="cpd-save-search__preview-src"
                      >
                        source ↗
                      </a>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
      {status && (
        <p
          className={status.kind === "ok" ? "cpd-auth__ok" : "cpd-auth__error"}
          role={status.kind === "ok" ? "status" : "alert"}
        >
          {status.message}
        </p>
      )}
    </div>
  );
}
