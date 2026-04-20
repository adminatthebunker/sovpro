import { useState } from "react";
import { Link } from "react-router-dom";
import { useAdminFetch } from "../../hooks/useAdminFetch";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { adminFetch } from "../../api";
import "../../styles/admin.css";

type Status = "pending" | "triaged" | "applied" | "rejected" | "duplicate" | "spam";
type ViewFilter = Status | "all";

interface Correction {
  id: string;
  subject_type: "speech" | "bill" | "politician" | "vote" | "organization" | "general";
  subject_id: string | null;
  issue: string;
  proposed_fix: string | null;
  evidence_url: string | null;
  status: Status;
  reviewer_notes: string | null;
  reviewed_by: string | null;
  submitter_name: string | null;
  submitter_email: string | null;
  user_id: string | null;
  source: "web" | "email" | "api";
  received_at: string;
  resolved_at: string | null;
  user_email: string | null;
  user_display_name: string | null;
  politician_name: string | null;
}

type StatsResp = Record<Status, number>;
interface ListResp { corrections: Correction[] }

const FILTERS: Array<{ value: ViewFilter; label: string }> = [
  { value: "pending", label: "Pending" },
  { value: "triaged", label: "Triaged" },
  { value: "applied", label: "Applied" },
  { value: "rejected", label: "Rejected" },
  { value: "duplicate", label: "Duplicate" },
  { value: "spam", label: "Spam" },
  { value: "all", label: "All" },
];

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="admin__stat">
      <div className="admin__stat-value">{value}</div>
      <div className="admin__stat-label">{label}</div>
    </div>
  );
}

function subjectLink(c: Correction): { href: string | null; label: string } {
  if (!c.subject_id) return { href: null, label: c.subject_type };
  switch (c.subject_type) {
    case "politician":
      return { href: `/politicians/${c.subject_id}`, label: c.politician_name || c.subject_id };
    case "speech":
      return { href: `/speeches/${c.subject_id}`, label: `speech ${c.subject_id.slice(0, 8)}` };
    default:
      return { href: null, label: `${c.subject_type} ${c.subject_id.slice(0, 8)}` };
  }
}

export default function AdminCorrections() {
  useDocumentTitle("Admin · Corrections");
  const [filter, setFilter] = useState<ViewFilter>("pending");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const stats = useAdminFetch<StatsResp>("/corrections/stats", { pollMs: 15000 });
  const list = useAdminFetch<ListResp>(`/corrections?status=${filter}&limit=100`);

  async function setStatus(c: Correction, status: Status, reviewer_notes?: string) {
    const destructive = status === "rejected" || status === "spam";
    if (destructive && !confirm(`Mark "${c.issue.slice(0, 60)}…" as ${status}?`)) return;
    setBusy(c.id);
    try {
      await adminFetch(`/corrections/${c.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status,
          reviewer_notes: reviewer_notes?.trim() || null,
        }),
      });
      list.refresh();
      stats.refresh();
      setExpanded(null);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="admin__panel">
      <header className="admin__panel-head">
        <h3>Corrections</h3>
        <p className="admin__hint">
          User-submitted corrections. Mark each as applied, rejected, duplicate, or
          spam — or triage first and come back later.
        </p>
      </header>

      <div className="admin__stats-grid">
        <Stat label="Pending" value={stats.data?.pending ?? 0} />
        <Stat label="Triaged" value={stats.data?.triaged ?? 0} />
        <Stat label="Applied" value={stats.data?.applied ?? 0} />
        <Stat label="Rejected" value={stats.data?.rejected ?? 0} />
        <Stat label="Duplicate" value={stats.data?.duplicate ?? 0} />
        <Stat label="Spam" value={stats.data?.spam ?? 0} />
      </div>

      <div className="admin__toolbar">
        {FILTERS.map(f => (
          <button
            key={f.value}
            type="button"
            className={f.value === filter ? "admin__tab active" : "admin__tab"}
            onClick={() => setFilter(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {list.loading && <p className="admin__hint">Loading…</p>}
      {list.error && <p className="admin__error" role="alert">{list.error.message}</p>}

      {list.data && list.data.corrections.length === 0 && (
        <p className="admin__hint">No corrections matching this filter.</p>
      )}

      {list.data && list.data.corrections.length > 0 && (
        <table className="admin__table">
          <thead>
            <tr>
              <th>Received</th>
              <th>Subject</th>
              <th>Submitter</th>
              <th>Issue</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {list.data.corrections.map(c => {
              const isOpen = expanded === c.id;
              const submitter = c.user_id
                ? `⟨signed in⟩ ${c.user_display_name || c.user_email}`
                : (c.submitter_name || c.submitter_email || "anonymous");
              const subj = subjectLink(c);
              return (
                <>
                  <tr key={c.id} className={isOpen ? "admin__row--open" : ""}>
                    <td>{new Date(c.received_at).toLocaleString()}</td>
                    <td>
                      {subj.href ? (
                        <Link to={subj.href}>{subj.label}</Link>
                      ) : (
                        subj.label
                      )}
                    </td>
                    <td>{submitter}</td>
                    <td className="admin__issue">
                      {c.issue.length > 120 ? c.issue.slice(0, 117) + "…" : c.issue}
                    </td>
                    <td>
                      <span className={`admin__status admin__status--${c.status}`}>
                        {c.status}
                      </span>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="admin__row-toggle"
                        onClick={() => setExpanded(isOpen ? null : c.id)}
                      >
                        {isOpen ? "Close" : "Review"}
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr key={`${c.id}-open`}>
                      <td colSpan={6}>
                        <div className="admin__review">
                          <dl className="admin__review-meta">
                            <dt>Issue</dt>
                            <dd>{c.issue}</dd>
                            {c.proposed_fix && <><dt>Proposed fix</dt><dd>{c.proposed_fix}</dd></>}
                            {c.evidence_url && (
                              <>
                                <dt>Evidence</dt>
                                <dd>
                                  <a href={c.evidence_url} target="_blank" rel="noopener noreferrer">
                                    {c.evidence_url}
                                  </a>
                                </dd>
                              </>
                            )}
                            {c.reviewer_notes && (
                              <><dt>Prior notes</dt><dd>{c.reviewer_notes}</dd></>
                            )}
                            {c.reviewed_by && (
                              <><dt>Last reviewer</dt><dd>{c.reviewed_by}</dd></>
                            )}
                          </dl>
                          <label>
                            <span>Reviewer notes (visible to submitter on their /account/corrections):</span>
                            <textarea
                              rows={3}
                              value={notes[c.id] ?? c.reviewer_notes ?? ""}
                              onChange={e => setNotes(n => ({ ...n, [c.id]: e.target.value }))}
                            />
                          </label>
                          <div className="admin__review-actions">
                            <button
                              type="button"
                              disabled={busy === c.id}
                              onClick={() => setStatus(c, "triaged", notes[c.id])}
                            >Triage</button>
                            <button
                              type="button"
                              disabled={busy === c.id}
                              onClick={() => setStatus(c, "applied", notes[c.id])}
                            >Mark applied</button>
                            <button
                              type="button"
                              disabled={busy === c.id}
                              onClick={() => setStatus(c, "rejected", notes[c.id])}
                            >Reject</button>
                            <button
                              type="button"
                              disabled={busy === c.id}
                              onClick={() => setStatus(c, "duplicate", notes[c.id])}
                            >Duplicate</button>
                            <button
                              type="button"
                              disabled={busy === c.id}
                              onClick={() => setStatus(c, "spam", notes[c.id])}
                            >Mark spam</button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
