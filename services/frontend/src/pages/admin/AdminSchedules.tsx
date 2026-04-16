import { useState } from "react";
import { useAdminFetch } from "../../hooks/useAdminFetch";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { adminFetch } from "../../api";
import { CommandForm } from "../../components/CommandForm";
import type { CommandsResponse, SchedulesResponse, ScheduleRow } from "../../types/admin";
import "../../styles/admin.css";

export default function AdminSchedules() {
  useDocumentTitle("Admin · Schedules");
  const commandsState = useAdminFetch<CommandsResponse>("/commands");
  const schedsState = useAdminFetch<SchedulesResponse>("/schedules", { pollMs: 10000 });
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [cron, setCron] = useState("0 3 * * *");
  const [busy, setBusy] = useState(false);

  const commands = commandsState.data?.commands ?? [];
  const schedules = schedsState.data?.schedules ?? [];

  async function submitSchedule(command: string, args: Record<string, unknown>) {
    if (!name.trim()) { alert("Name is required."); return; }
    setBusy(true);
    try {
      await adminFetch("/schedules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), command, args, cron, enabled: true }),
      });
      setCreating(false);
      setName("");
      schedsState.refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to create schedule.");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(s: ScheduleRow) {
    try {
      await adminFetch(`/schedules/${s.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !s.enabled }),
      });
      schedsState.refresh();
    } catch (e) { alert(e instanceof Error ? e.message : "Toggle failed."); }
  }

  async function remove(s: ScheduleRow) {
    if (!confirm(`Delete schedule "${s.name}"?`)) return;
    try {
      await adminFetch(`/schedules/${s.id}`, { method: "DELETE" });
      schedsState.refresh();
    } catch (e) { alert(e instanceof Error ? e.message : "Delete failed."); }
  }

  return (
    <div className="admin__content">
      <div className="admin__toolbar">
        <button onClick={() => setCreating(c => !c)}>
          {creating ? "Close" : "New schedule"}
        </button>
      </div>

      {creating && commands.length > 0 && (
        <div className="admin__panel">
          <label className="admin__cmd-arg">
            <span>Schedule name *</span>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. nightly federal Hansard" />
          </label>
          <CommandForm
            commands={commands}
            submitLabel="Create schedule"
            busy={busy}
            onSubmit={submitSchedule}
            extra={
              <label className="admin__cmd-arg">
                <span>Cron (UTC) *<small> — m h dom mon dow</small></span>
                <input value={cron} onChange={e => setCron(e.target.value)} placeholder="0 3 * * *" />
              </label>
            }
          />
        </div>
      )}

      {schedsState.error && (
        <div className="admin__error" role="alert">{schedsState.error.message}</div>
      )}

      <div className="admin__table-wrap">
        <table className="admin__table">
          <thead>
            <tr>
              <th></th>
              <th>Name</th>
              <th>Command</th>
              <th>Cron</th>
              <th>Last</th>
              <th>Next</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {schedules.length === 0 && (
              <tr><td colSpan={7} className="admin__empty">No schedules.</td></tr>
            )}
            {schedules.map(s => (
              <tr key={s.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    onChange={() => toggle(s)}
                    title={s.enabled ? "enabled" : "disabled"}
                  />
                </td>
                <td>{s.name}</td>
                <td><code>{s.command}</code></td>
                <td><code>{s.cron}</code></td>
                <td className="admin__muted">
                  {s.last_enqueued_at ? new Date(s.last_enqueued_at).toLocaleString() : "—"}
                </td>
                <td className="admin__muted">
                  {s.next_run_at ? new Date(s.next_run_at).toLocaleString() : "—"}
                </td>
                <td>
                  <button className="admin__danger admin__danger--sm" onClick={() => remove(s)}>
                    delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
