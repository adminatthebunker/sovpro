import { useCallback, useEffect, useState } from "react";
import { adminFetch } from "../../api";

interface AdminReportRow {
  id: string;
  user_id: string;
  user_email: string;
  politician_id: string;
  politician_name: string | null;
  query: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "refunded";
  estimated_credits: number;
  chunk_count_actual: number | null;
  model_used: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  hold_ledger_id: string | null;
}

interface AdminBugRow {
  id: string;
  report_id: string;
  user_id: string;
  user_email: string;
  politician_id: string;
  politician_name: string | null;
  report_query: string;
  message: string;
  status: "open" | "reviewing" | "resolved" | "dismissed";
  admin_notes: string | null;
  created_at: string;
  resolved_at: string | null;
}

const STATUSES = ["all", "queued", "running", "succeeded", "failed", "refunded"] as const;
const BUG_STATUSES = ["all", "open", "reviewing", "resolved", "dismissed"] as const;

export default function AdminReports() {
  const [statusFilter, setStatusFilter] = useState<typeof STATUSES[number]>("all");
  const [q, setQ] = useState("");
  const [reports, setReports] = useState<AdminReportRow[]>([]);
  const [reportsErr, setReportsErr] = useState<string | null>(null);
  const [reportsLoading, setReportsLoading] = useState(true);

  const [bugStatusFilter, setBugStatusFilter] = useState<typeof BUG_STATUSES[number]>("open");
  const [bugs, setBugs] = useState<AdminBugRow[]>([]);
  const [bugsErr, setBugsErr] = useState<string | null>(null);

  const loadReports = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (statusFilter !== "all") params.set("status", statusFilter);
      if (q.trim()) params.set("q", q.trim());
      const rows = await adminFetch<AdminReportRow[]>(`/reports?${params}`);
      setReports(rows);
      setReportsErr(null);
    } catch (e) {
      setReportsErr(e instanceof Error ? e.message : "Failed to load reports");
    } finally {
      setReportsLoading(false);
    }
  }, [statusFilter, q]);

  const loadBugs = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (bugStatusFilter !== "all") params.set("status", bugStatusFilter);
      const rows = await adminFetch<AdminBugRow[]>(`/bug-reports?${params}`);
      setBugs(rows);
      setBugsErr(null);
    } catch (e) {
      setBugsErr(e instanceof Error ? e.message : "Failed to load bug reports");
    }
  }, [bugStatusFilter]);

  useEffect(() => {
    void loadReports();
  }, [loadReports]);

  useEffect(() => {
    void loadBugs();
  }, [loadBugs]);

  const refundReport = useCallback(
    async (id: string) => {
      const reason = prompt(
        "Refund reason (will appear in the user's ledger history):"
      );
      if (!reason || reason.trim().length < 3) return;
      try {
        const res = await adminFetch<{
          refunded: boolean;
          mode: "released_hold" | "compensating_admin_credit";
          credits: number;
        }>(`/reports/${id}/refund`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: reason.trim() }),
        });
        alert(
          `Refunded ${res.credits} credits via ${
            res.mode === "released_hold"
              ? "hold release"
              : "compensating admin credit"
          }.`
        );
        await loadReports();
      } catch (e) {
        alert(`Refund failed: ${e instanceof Error ? e.message : "unknown"}`);
      }
    },
    [loadReports]
  );

  const updateBug = useCallback(
    async (
      id: string,
      patch: { status?: AdminBugRow["status"]; admin_notes?: string | null }
    ) => {
      try {
        await adminFetch(`/bug-reports/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        });
        await loadBugs();
      } catch (e) {
        alert(`Update failed: ${e instanceof Error ? e.message : "unknown"}`);
      }
    },
    [loadBugs]
  );

  return (
    <div className="admin-reports">
      <h2>Reports</h2>

      <section>
        <h3>Job queue</h3>
        <div className="admin-reports__filters">
          <label>
            Status:&nbsp;
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as typeof STATUSES[number])}
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            Search:&nbsp;
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="email or query substring"
              onKeyDown={(e) => e.key === "Enter" && loadReports()}
            />
          </label>
          <button onClick={loadReports}>Refresh</button>
        </div>

        {reportsErr && <p role="alert">{reportsErr}</p>}
        {reportsLoading ? (
          <p>Loading…</p>
        ) : (
          <table className="admin-reports__table">
            <thead>
              <tr>
                <th>Created</th>
                <th>User</th>
                <th>Politician</th>
                <th>Query</th>
                <th>Status</th>
                <th>Credits</th>
                <th>Chunks</th>
                <th>Tokens</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.id}>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                  <td>{r.user_email}</td>
                  <td>{r.politician_name ?? r.politician_id}</td>
                  <td>{r.query}</td>
                  <td>
                    <span className={`admin-reports__status admin-reports__status--${r.status}`}>
                      {r.status}
                    </span>
                    {r.error && (
                      <div className="admin-reports__err">{r.error}</div>
                    )}
                  </td>
                  <td>{r.estimated_credits}</td>
                  <td>{r.chunk_count_actual ?? "—"}</td>
                  <td>
                    {r.tokens_in ?? "—"} / {r.tokens_out ?? "—"}
                  </td>
                  <td>
                    {(r.status === "succeeded" ||
                      r.status === "failed" ||
                      r.status === "running") && (
                      <button onClick={() => refundReport(r.id)}>Refund</button>
                    )}
                  </td>
                </tr>
              ))}
              {!reports.length && (
                <tr>
                  <td colSpan={9}>No reports match the current filter.</td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </section>

      <section style={{ marginTop: "2rem" }}>
        <h3>Bug reports</h3>
        <div className="admin-reports__filters">
          <label>
            Status:&nbsp;
            <select
              value={bugStatusFilter}
              onChange={(e) => setBugStatusFilter(e.target.value as typeof BUG_STATUSES[number])}
            >
              {BUG_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <button onClick={loadBugs}>Refresh</button>
        </div>
        {bugsErr && <p role="alert">{bugsErr}</p>}
        <table className="admin-reports__table">
          <thead>
            <tr>
              <th>Submitted</th>
              <th>User</th>
              <th>Report</th>
              <th>Message</th>
              <th>Status</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {bugs.map((b) => (
              <tr key={b.id}>
                <td>{new Date(b.created_at).toLocaleString()}</td>
                <td>{b.user_email}</td>
                <td>
                  <div>{b.politician_name ?? b.politician_id}</div>
                  <div style={{ color: "#888", fontSize: "0.85em" }}>"{b.report_query}"</div>
                </td>
                <td style={{ maxWidth: "320px", whiteSpace: "pre-wrap" }}>{b.message}</td>
                <td>
                  <select
                    value={b.status}
                    onChange={(e) =>
                      updateBug(b.id, { status: e.target.value as AdminBugRow["status"] })
                    }
                  >
                    {BUG_STATUSES.filter((s) => s !== "all").map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <textarea
                    rows={2}
                    defaultValue={b.admin_notes ?? ""}
                    onBlur={(e) =>
                      e.target.value !== (b.admin_notes ?? "") &&
                      updateBug(b.id, { admin_notes: e.target.value || null })
                    }
                  />
                </td>
              </tr>
            ))}
            {!bugs.length && (
              <tr>
                <td colSpan={6}>No bug reports match the current filter.</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
