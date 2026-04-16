import { Link, useParams } from "react-router-dom";
import { useAdminFetch } from "../../hooks/useAdminFetch";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { adminFetch } from "../../api";
import type { JobRow } from "../../types/admin";
import "../../styles/admin.css";

export default function AdminJobDetail() {
  const { id } = useParams<{ id: string }>();
  useDocumentTitle(`Admin · Job ${id?.slice(0, 8)}`);
  const { data, error, loading, refresh } = useAdminFetch<JobRow>(
    id ? `/jobs/${id}` : null,
    { pollMs: 3000 }
  );

  async function cancel() {
    if (!id) return;
    try {
      await adminFetch<{ status: string }>(`/jobs/${id}/cancel`, { method: "POST" });
      refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Cancel failed.");
    }
  }

  if (error) return <div className="admin__error" role="alert">{error.message}</div>;
  if (loading && !data) return <p className="admin__empty">Loading…</p>;
  if (!data) return null;

  const runtime = data.started_at && data.finished_at
    ? `${((new Date(data.finished_at).getTime() - new Date(data.started_at).getTime()) / 1000).toFixed(1)}s`
    : data.started_at
      ? `${((Date.now() - new Date(data.started_at).getTime()) / 1000).toFixed(0)}s (running)`
      : "—";

  return (
    <div className="admin__content">
      <p><Link to="/admin/jobs">← back to jobs</Link></p>
      <h3>
        <code>{data.command}</code>{" "}
        <small className="admin__muted">{data.id}</small>
      </h3>
      <dl className="admin__kv">
        <dt>Status</dt><dd><span className={`admin__pill admin__pill--${
          data.status === "succeeded" ? "ok"
            : data.status === "failed" ? "err"
            : data.status === "running" ? "running"
            : data.status === "queued" ? "queued" : "mute"
        }`}>{data.status}</span></dd>
        <dt>Exit code</dt><dd>{data.exit_code ?? "—"}</dd>
        <dt>Queued</dt><dd>{new Date(data.queued_at).toLocaleString()}</dd>
        <dt>Started</dt><dd>{data.started_at ? new Date(data.started_at).toLocaleString() : "—"}</dd>
        <dt>Finished</dt><dd>{data.finished_at ? new Date(data.finished_at).toLocaleString() : "—"}</dd>
        <dt>Runtime</dt><dd>{runtime}</dd>
        <dt>Requested by</dt><dd>{data.requested_by ?? "—"}</dd>
        <dt>Schedule</dt><dd>{data.schedule_id ?? "—"}</dd>
        <dt>Priority</dt><dd>{data.priority}</dd>
        <dt>Args</dt><dd><pre className="admin__pre">{JSON.stringify(data.args ?? {}, null, 2)}</pre></dd>
        {data.error && (<><dt>Worker error</dt><dd className="admin__error-inline">{data.error}</dd></>)}
      </dl>

      {data.status === "queued" && (
        <button className="admin__danger" onClick={cancel}>Cancel (still queued)</button>
      )}

      <h4>stdout (last 4 KB)</h4>
      <pre className="admin__pre admin__pre--log">{data.stdout_tail || "<empty>"}</pre>

      <h4>stderr (last 4 KB)</h4>
      <pre className="admin__pre admin__pre--log">{data.stderr_tail || "<empty>"}</pre>
    </div>
  );
}
