import { Link } from "react-router-dom";
import { useAdminFetch } from "../../hooks/useAdminFetch";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import type { AdminStats } from "../../types/admin";
import "../../styles/admin.css";

function Stat({ label, value, sub }: { label: string; value: number | string; sub?: string }) {
  return (
    <div className="admin__stat">
      <div className="admin__stat-value">{value}</div>
      <div className="admin__stat-label">{label}</div>
      {sub && <div className="admin__stat-sub">{sub}</div>}
    </div>
  );
}

export default function AdminDashboard() {
  useDocumentTitle("Admin · Dashboard");
  const { data, loading, error } = useAdminFetch<AdminStats>("/stats", { pollMs: 5000 });

  if (error) {
    return <div className="admin__error" role="alert">Failed to load stats: {error.message}</div>;
  }
  if (loading && !data) {
    return <p className="admin__empty">Loading stats…</p>;
  }
  if (!data) return null;

  return (
    <div className="admin__content">
      <div className="admin__stats-grid">
        <Stat label="Speeches" value={data.speeches.toLocaleString()} />
        <Stat
          label="Speech chunks"
          value={data.chunks.total.toLocaleString()}
          sub={`${data.chunks.embedded.toLocaleString()} embedded · ${data.chunks.pending.toLocaleString()} pending`}
        />
        <Stat label="Running" value={data.jobs.running} sub={`${data.jobs.queued} queued`} />
        <Stat
          label="Jobs (24h)"
          value={`${data.jobs.succeeded_24h} ✓`}
          sub={data.jobs.failed_24h ? `${data.jobs.failed_24h} ✗ failed` : "no failures"}
        />
        <Stat
          label="Jurisdictions"
          value={`${data.jurisdictions.live}/${data.jurisdictions.total}`}
          sub="bills pipelines live"
        />
      </div>

      {data.recent_failures.length > 0 && (
        <section className="admin__section">
          <h3>Recent failures (24h)</h3>
          <ul className="admin__failure-list">
            {data.recent_failures.map(f => (
              <li key={f.id}>
                <Link to={`/admin/jobs/${f.id}`}>
                  <code>{f.command}</code>
                </Link>{" "}
                — {new Date(f.finished_at).toLocaleString()}{" "}
                {f.error && <span className="admin__error-inline">· {f.error}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="admin__section">
        <p>
          <Link to="/admin/jobs">→ Jobs</Link>{" · "}
          <Link to="/admin/schedules">→ Schedules</Link>
        </p>
      </section>
    </div>
  );
}
