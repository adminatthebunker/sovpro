import { useState } from "react";
import { Link } from "react-router-dom";
import { useAdminFetch } from "../../hooks/useAdminFetch";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { adminFetch } from "../../api";
import { CommandForm } from "../../components/CommandForm";
import type {
  CommandsResponse, JobsListResponse, JobStatus,
} from "../../types/admin";
import "../../styles/admin.css";

const STATUS_CLASS: Record<JobStatus, string> = {
  queued: "admin__pill admin__pill--queued",
  running: "admin__pill admin__pill--running",
  succeeded: "admin__pill admin__pill--ok",
  failed: "admin__pill admin__pill--err",
  cancelled: "admin__pill admin__pill--mute",
};

export default function AdminJobs() {
  useDocumentTitle("Admin · Jobs");
  const [statusFilter, setStatusFilter] = useState<JobStatus | "">("");
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);

  const commandsState = useAdminFetch<CommandsResponse>("/commands");
  const jobsPath = statusFilter ? `/jobs?status=${statusFilter}&limit=100` : "/jobs?limit=100";
  const jobsState = useAdminFetch<JobsListResponse>(jobsPath, { pollMs: 3000 });

  async function submitJob(command: string, args: Record<string, unknown>) {
    setBusy(true);
    try {
      await adminFetch<{ id: string }>("/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command, args, priority: 10 }),
      });
      setShowForm(false);
      jobsState.refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to queue job.");
    } finally {
      setBusy(false);
    }
  }

  const jobs = jobsState.data?.jobs ?? [];
  const commands = commandsState.data?.commands ?? [];

  return (
    <div className="admin__content">
      <div className="admin__toolbar">
        <label>
          Filter:{" "}
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value as JobStatus | "")}>
            <option value="">All</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </label>
        <button onClick={() => setShowForm(s => !s)}>
          {showForm ? "Close" : "Run a command"}
        </button>
      </div>

      {showForm && commands.length > 0 && (
        <div className="admin__panel">
          <CommandForm
            commands={commands}
            submitLabel="Queue job"
            busy={busy}
            onSubmit={submitJob}
          />
        </div>
      )}

      {jobsState.error && (
        <div className="admin__error" role="alert">Failed to load: {jobsState.error.message}</div>
      )}

      <div className="admin__table-wrap">
        <table className="admin__table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Command</th>
              <th>Requested by</th>
              <th>Queued</th>
              <th>Finished</th>
              <th>Exit</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 && (
              <tr><td colSpan={7} className="admin__empty">No jobs.</td></tr>
            )}
            {jobs.map(j => (
              <tr key={j.id}>
                <td><span className={STATUS_CLASS[j.status]}>{j.status}</span></td>
                <td><code>{j.command}</code></td>
                <td className="admin__muted">{j.requested_by ?? "—"}</td>
                <td className="admin__muted">{new Date(j.queued_at).toLocaleString()}</td>
                <td className="admin__muted">
                  {j.finished_at ? new Date(j.finished_at).toLocaleString() : "—"}
                </td>
                <td className="admin__muted">{j.exit_code ?? "—"}</td>
                <td><Link to={`/admin/jobs/${j.id}`}>details →</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
