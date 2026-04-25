import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  userFetch,
  UserUnauthorizedError,
  UserAuthDisabledError,
  type ReportDetail,
} from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import "../styles/reports.css";

function buildCitation(report: ReportDetail, url: string): string {
  const year = (report.finished_at ? new Date(report.finished_at) : new Date(report.created_at)).getFullYear();
  const polName = report.politician_name ?? "Unknown politician";
  const party = report.politician_party ? ` (${report.politician_party})` : "";
  return `Canadian Political Data. (${year}). ${polName}${party} on "${report.query}" [premium report]. Retrieved from ${url}`;
}

/**
 * /reports/:id — viewer for a single full report.
 *
 * Two viewing modes:
 *   - Owner: signed in, owns the report. Sees download button, public
 *     toggle, citation block, bug-report form. Fetched via /me/reports/:id.
 *   - Public: anyone (signed in or not) viewing a report whose owner
 *     has flipped is_public = true. Sees download + citation but no
 *     toggle and no bug-form. Fetched via /reports/public/:id.
 *
 * Mounts OUTSIDE the main Layout (mirroring InvoicePage) so the
 * rendered report has a clean print canvas: no site nav, no footer,
 * generous typography. The summary lives at the top; the model HTML
 * (sanitised server-side; allowlist of p/h2/h3/ul/ol/li/blockquote/em/
 * strong/a[href] only) renders below via dangerouslySetInnerHTML.
 *
 * 404 is the right answer for non-owner-non-public — id-enumeration
 * discipline.
 */
export default function ReportViewerPage() {
  const { id } = useParams<{ id: string }>();
  const { user, loading: authLoading } = useUserAuth();
  const [report, setReport] = useState<ReportDetail | null>(null);
  const [isOwner, setIsOwner] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [bugOpen, setBugOpen] = useState(false);
  const [bugMessage, setBugMessage] = useState("");
  const [bugSubmitting, setBugSubmitting] = useState(false);
  const [bugSent, setBugSent] = useState(false);
  const [bugError, setBugError] = useState<string | null>(null);

  const [togglingPublic, setTogglingPublic] = useState(false);
  const [toggleError, setToggleError] = useState<string | null>(null);

  const [copied, setCopied] = useState<"link" | "citation" | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    // Owner-first when signed in: try /me/reports/:id, fall back to
    // the public endpoint on 404 (not-owner). Anonymous visitors skip
    // the owner attempt entirely.
    const tryOwner = async (): Promise<{ report: ReportDetail } | "miss"> => {
      try {
        return await userFetch<{ report: ReportDetail }>(`/me/reports/${id}`);
      } catch (e) {
        if (e instanceof Error && /^404\b/.test(e.message)) return "miss";
        if (e instanceof UserUnauthorizedError) return "miss";
        throw e;
      }
    };
    const tryPublic = async (): Promise<{ report: ReportDetail } | "miss"> => {
      try {
        return await userFetch<{ report: ReportDetail }>(`/reports/public/${id}`);
      } catch (e) {
        if (e instanceof Error && /^404\b/.test(e.message)) return "miss";
        throw e;
      }
    };
    try {
      if (user) {
        const owned = await tryOwner();
        if (owned !== "miss") {
          setReport(owned.report);
          setIsOwner(true);
          setError(null);
          return;
        }
      }
      const pub = await tryPublic();
      if (pub !== "miss") {
        setReport(pub.report);
        setIsOwner(false);
        setError(null);
        return;
      }
      setError("Report not found.");
    } catch (e) {
      if (e instanceof UserAuthDisabledError) setError("User accounts are disabled on this server.");
      else if (e instanceof Error) setError(e.message);
      else setError("Failed to load report.");
    } finally {
      setLoading(false);
    }
  }, [id, user]);

  useEffect(() => {
    if (authLoading) return;
    void load();
  }, [authLoading, load]);

  // Poll while still in-flight so the user sees the report appear
  // when the worker finishes — they may have arrived from the email
  // link before the worker completed. 4s matches the list page.
  useEffect(() => {
    if (!report) return;
    if (report.status !== "queued" && report.status !== "running") return;
    const t = window.setInterval(load, 4000);
    return () => window.clearInterval(t);
  }, [report, load]);

  // Pre-fill a sensible filename for the browser's Save-as-PDF dialog.
  useEffect(() => {
    if (!report || report.status !== "succeeded") return;
    const previous = document.title;
    const polName = report.politician_name ?? "Report";
    const topic = report.query ? ` — "${report.query}"` : "";
    document.title = `${polName}${topic} — Canadian Political Data`;
    return () => {
      document.title = previous;
    };
  }, [report]);

  const handlePrint = useCallback(() => {
    window.print();
  }, []);

  const togglePublic = useCallback(async () => {
    if (!id || !report) return;
    setTogglingPublic(true);
    setToggleError(null);
    try {
      const next = !report.is_public;
      await userFetch(`/me/reports/${id}/visibility`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_public: next }),
      });
      setReport({ ...report, is_public: next });
    } catch (e) {
      setToggleError(e instanceof Error ? e.message : "Failed to update visibility.");
    } finally {
      setTogglingPublic(false);
    }
  }, [id, report]);

  const reportUrl = typeof window !== "undefined" && id ? `${window.location.origin}/reports/${id}` : "";

  const citation = report
    ? buildCitation(report, reportUrl)
    : "";

  const copyToClipboard = useCallback(async (text: string, kind: "link" | "citation") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(kind);
      window.setTimeout(() => setCopied((c) => (c === kind ? null : c)), 2000);
    } catch {
      // Clipboard API may be blocked (e.g., insecure context); fall back
      // to a manual selection prompt by surfacing the text in toggleError
      // — minor enough that we don't need a dedicated error slot.
      setToggleError("Copy failed — your browser blocked clipboard access.");
    }
  }, []);

  const submitBug = useCallback(async () => {
    if (!id) return;
    if (bugMessage.trim().length < 10) {
      setBugError("Please describe the issue (at least 10 characters).");
      return;
    }
    setBugSubmitting(true);
    setBugError(null);
    try {
      await userFetch(`/me/reports/${id}/bug-report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: bugMessage }),
      });
      setBugSent(true);
      setBugOpen(false);
      setBugMessage("");
    } catch (e) {
      setBugError(e instanceof Error ? e.message : "Failed to submit bug report.");
    } finally {
      setBugSubmitting(false);
    }
  }, [id, bugMessage]);

  if (authLoading || loading) {
    return (
      <main className="cpd-report-viewer">
        <div className="cpd-report-viewer__inner">
          <p>Loading…</p>
        </div>
      </main>
    );
  }
  if (error || !report) {
    return (
      <main className="cpd-report-viewer">
        <div className="cpd-report-viewer__inner">
          <p role="alert">{error ?? "Report not found."}</p>
          <p>
            {user ? (
              <Link to="/account/reports">← Back to your reports</Link>
            ) : (
              <Link to="/">← Canadian Political Data</Link>
            )}
          </p>
        </div>
      </main>
    );
  }

  const isInflight = report.status === "queued" || report.status === "running";

  return (
    <main className="cpd-report-viewer">
      <div className="cpd-report-viewer__inner">
      <header className="cpd-report-viewer__head">
        <div className="cpd-report-viewer__head-actions">
          {isOwner ? (
            <Link to="/account/reports" className="cpd-report-viewer__back">
              ← Your reports
            </Link>
          ) : user ? (
            <Link to="/account/reports" className="cpd-report-viewer__back">
              ← Your reports
            </Link>
          ) : (
            <Link to="/" className="cpd-report-viewer__back">
              ← Canadian Political Data
            </Link>
          )}
          <div className="cpd-report-viewer__head-buttons">
            {isOwner && report.status === "succeeded" && (
              <button
                type="button"
                className={`cpd-report-viewer__public-toggle${
                  report.is_public ? " cpd-report-viewer__public-toggle--on" : ""
                }`}
                onClick={togglePublic}
                disabled={togglingPublic}
                aria-pressed={report.is_public}
                title={
                  report.is_public
                    ? "Anyone with the link can view this report. Click to make private."
                    : "Only you can view this report. Click to share publicly."
                }
              >
                <span className="cpd-report-viewer__public-toggle-dot" aria-hidden />
                {report.is_public ? "Public" : "Private"}
              </button>
            )}
            {report.status === "succeeded" && report.html && (
              <button
                type="button"
                className="cpd-report-viewer__download"
                onClick={handlePrint}
              >
                Download Report
              </button>
            )}
          </div>
        </div>
        {toggleError && (
          <p className="cpd-report-viewer__toggle-error" role="alert">{toggleError}</p>
        )}
        <h1>
          {report.politician_name ?? "Unknown politician"}
          {report.politician_party && (
            <span className="cpd-report-viewer__party"> ({report.politician_party})</span>
          )}
        </h1>
        <p className="cpd-report-viewer__topic">
          on <em>"{report.query}"</em>
        </p>
        <div className="cpd-report-viewer__meta">
          {report.chunk_count_actual !== null && (
            <span>{report.chunk_count_actual} quotes analysed</span>
          )}
          {report.model_used && <span>Model: {report.model_used}</span>}
          <span>Status: {report.status}</span>
        </div>
      </header>

      {isInflight && (
        <p className="cpd-report-viewer__inflight">
          Your report is still generating. This page will update automatically when it's ready.
        </p>
      )}

      {report.status === "failed" && (
        <div className="cpd-report-viewer__failed">
          <p><strong>Report generation failed.</strong> {report.error}</p>
          <p>Your credits have been refunded automatically.</p>
        </div>
      )}

      {report.summary && (
        <p className="cpd-report-viewer__summary">{report.summary}</p>
      )}

      {report.html && (
        <article
          className="cpd-report-viewer__body"
          // SAFETY: html is sanitised server-side in services/api/src/lib/reports.ts
          // (sanitize-html allowlist of p/h2/h3/ul/ol/li/blockquote/em/strong/a[href]
          // restricted to internal /speeches/...#chunk-... paths).
          dangerouslySetInnerHTML={{ __html: report.html }}
        />
      )}

      {report.status === "succeeded" && (
        <footer className="cpd-report-viewer__foot">
          <p className="cpd-report-viewer__disclaimer">
            This report is a model-generated synthesis of public Hansard records.
            Every claim links back to a source quote — read the quotes before
            drawing conclusions. Canadian Political Data is not responsible for
            conclusions drawn from this brief.
          </p>

          <div className="cpd-report-viewer__cite">
            <h3 className="cpd-report-viewer__cite-heading">Cite this report</h3>
            <p className="cpd-report-viewer__cite-text">{citation}</p>
            <div className="cpd-report-viewer__cite-actions">
              <button
                type="button"
                className="cpd-report-viewer__cite-button"
                onClick={() => copyToClipboard(citation, "citation")}
              >
                {copied === "citation" ? "Copied" : "Copy citation"}
              </button>
              <button
                type="button"
                className="cpd-report-viewer__cite-button"
                onClick={() => copyToClipboard(reportUrl, "link")}
              >
                {copied === "link" ? "Copied" : "Copy link"}
              </button>
            </div>
            {isOwner && !report.is_public && (
              <p className="cpd-report-viewer__cite-hint">
                Tip: this report is currently <strong>private</strong> — anyone
                you share the link with will see a "report not found" page until
                you flip the toggle above to <strong>Public</strong>.
              </p>
            )}
          </div>

          {isOwner && (bugSent ? (
            <p className="cpd-report-viewer__bug-sent">
              Thanks — we'll review your report shortly.
            </p>
          ) : bugOpen ? (
            <div className="cpd-report-viewer__bug-form">
              <label htmlFor="bug-message">What's wrong with this report?</label>
              <textarea
                id="bug-message"
                rows={4}
                value={bugMessage}
                onChange={(e) => setBugMessage(e.target.value)}
                placeholder="Be as specific as you can — quote misattributed, missing context, fabrication, etc."
                disabled={bugSubmitting}
              />
              {bugError && <p role="alert">{bugError}</p>}
              <div className="cpd-report-viewer__bug-actions">
                <button
                  type="button"
                  onClick={() => {
                    setBugOpen(false);
                    setBugMessage("");
                    setBugError(null);
                  }}
                  disabled={bugSubmitting}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={submitBug}
                  disabled={bugSubmitting}
                  className="cpd-report-viewer__bug-submit"
                >
                  {bugSubmitting ? "Submitting…" : "Submit"}
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              className="cpd-report-viewer__bug-open"
              onClick={() => setBugOpen(true)}
            >
              Report a bug
            </button>
          ))}
        </footer>
      )}
      </div>
    </main>
  );
}
