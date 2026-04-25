import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  userFetch,
  UserUnauthorizedError,
  UserAuthDisabledError,
  type ReportListEntry,
} from "../api";
import { useUserAuth } from "../hooks/useUserAuth";

const STATUS_LABEL: Record<ReportListEntry["status"], string> = {
  queued: "Queued",
  running: "Generating…",
  succeeded: "Ready",
  failed: "Failed",
  cancelled: "Cancelled",
  refunded: "Refunded",
};

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return d.toLocaleDateString();
}

function formatWordCount(n: number | null): string | null {
  if (n === null || n === undefined) return null;
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k words`;
  return `${n} words`;
}

function formatDuration(startIso: string, endIso: string | null): string | null {
  if (!endIso) return null;
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (ms < 0) return null;
  if (ms < 60_000) return `${Math.round(ms / 1000)}s to generate`;
  const mins = ms / 60_000;
  if (mins < 60) return `${mins.toFixed(mins < 10 ? 1 : 0).replace(/\.0$/, "")} min to generate`;
  const hrs = mins / 60;
  return `${hrs.toFixed(1).replace(/\.0$/, "")} h to generate`;
}

function shortenModel(m: string | null): string | null {
  if (!m) return null;
  // anthropic/claude-4.6-sonnet-20260217 → claude-4.6-sonnet
  const last = m.split("/").pop() ?? m;
  return last.replace(/-\d{8}$/, "");
}

/**
 * /account/reports — caller's report history.
 *
 * Polls /me/reports every 4 seconds while any row is still queued or
 * running. Once everything settles, polling stops to keep the page
 * cheap on the API.
 *
 * ?new=<id> highlights a freshly-submitted row so the user knows their
 * click landed; the highlight clears when the row becomes terminal.
 */
export default function ReportsListPage() {
  const { user, loading: authLoading } = useUserAuth();
  const [params] = useSearchParams();
  const newId = params.get("new");
  const [reports, setReports] = useState<ReportListEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const res = await userFetch<{ reports: ReportListEntry[] }>("/me/reports");
      setReports(res.reports);
      setError(null);
    } catch (e) {
      if (e instanceof UserUnauthorizedError) setError("Sign in to view your reports.");
      else if (e instanceof UserAuthDisabledError) setError("User accounts are disabled on this server.");
      else if (e instanceof Error) setError(e.message);
      else setError("Failed to load reports.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      setLoading(false);
      return;
    }
    void refresh();
  }, [user, authLoading, refresh]);

  const hasInflight = useMemo(
    () => reports.some((r) => r.status === "queued" || r.status === "running"),
    [reports]
  );

  useEffect(() => {
    if (!hasInflight) return;
    const t = window.setInterval(refresh, 4000);
    return () => window.clearInterval(t);
  }, [hasInflight, refresh]);

  if (authLoading || loading) {
    return (
      <main className="cpd-auth cpd-auth--reports">
        <div className="cpd-auth__container">
          <p className="cpd-auth__muted">Loading…</p>
        </div>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="cpd-auth cpd-auth--reports">
        <div className="cpd-auth__container">
          <h1>Reports</h1>
          <p>
            <Link to="/login" className="cpd-auth__link">
              Sign in
            </Link>{" "}
            to submit and view full reports.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="cpd-auth cpd-auth--reports">
      <div className="cpd-auth__container">
        <header className="cpd-auth__header">
          <h1>Your reports</h1>
          <p className="cpd-auth__muted">
            Premium reports synthesise every matching quote from a politician on a topic
            into one navigable brief. Each report links every claim back to its source —
            read the quotes before drawing conclusions.
          </p>
        </header>

        {error && (
          <p className="cpd-auth__error" role="alert">
            {error}
          </p>
        )}

        {reports.length === 0 ? (
          <div className="cpd-auth__empty">
            <p>You haven't generated any reports yet.</p>
            <p className="cpd-auth__muted">
              From the <Link to="/search">search</Link> page, switch to{" "}
              <strong>By politician</strong>, find a politician card, and click{" "}
              <strong>"Full report — analyze everything"</strong>.
            </p>
          </div>
        ) : (
          <ul className="cpd-reports-list">
            {reports.map((r) => {
              const isNew = r.id === newId;
              const isReady = r.status === "succeeded";
              const isInflight = r.status === "queued" || r.status === "running";
              return (
                <li
                  key={r.id}
                  className={`cpd-reports-list__item${
                    isNew ? " cpd-reports-list__item--new" : ""
                  } cpd-reports-list__item--${r.status}`}
                >
                  <div className="cpd-reports-list__head">
                    <strong>{r.politician_name ?? "Unknown politician"}</strong>
                    {r.politician_party && (
                      <span className="cpd-reports-list__party">({r.politician_party})</span>
                    )}
                    <span className="cpd-reports-list__topic">"{r.query}"</span>
                  </div>
                  <div className="cpd-reports-list__meta">
                    <span className={`cpd-reports-list__status cpd-reports-list__status--${r.status}`}>
                      {STATUS_LABEL[r.status]}
                    </span>
                    <span>{r.estimated_credits} credits</span>
                    <span>{formatRelative(r.created_at)}</span>
                    {isInflight && <span className="cpd-reports-list__spinner" aria-hidden />}
                  </div>
                  {isReady && (
                    <dl className="cpd-reports-list__stats">
                      {r.chunk_count_actual !== null && (
                        <div className="cpd-reports-list__stat">
                          <dt>Quotes analysed</dt>
                          <dd>{r.chunk_count_actual.toLocaleString()}</dd>
                        </div>
                      )}
                      {r.word_count !== null && (
                        <div className="cpd-reports-list__stat">
                          <dt>Length</dt>
                          <dd>{formatWordCount(r.word_count)}</dd>
                        </div>
                      )}
                      {formatDuration(r.created_at, r.finished_at) && (
                        <div className="cpd-reports-list__stat">
                          <dt>Generated</dt>
                          <dd>{formatDuration(r.created_at, r.finished_at)}</dd>
                        </div>
                      )}
                      {shortenModel(r.model_used) && (
                        <div className="cpd-reports-list__stat">
                          <dt>Model</dt>
                          <dd>{shortenModel(r.model_used)}</dd>
                        </div>
                      )}
                    </dl>
                  )}
                  {r.summary && (
                    <p className="cpd-reports-list__summary">{r.summary}</p>
                  )}
                  {r.error && (
                    <p className="cpd-reports-list__error">{r.error}</p>
                  )}
                  {isReady && (
                    <Link to={`/reports/${r.id}`} className="cpd-reports-list__view">
                      View report →
                    </Link>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </main>
  );
}
