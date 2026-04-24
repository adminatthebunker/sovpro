import { useState } from "react";
import { useAdminFetch } from "../../hooks/useAdminFetch";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { adminFetch } from "../../api";
import { safeHttpHref } from "../../lib/safe-href";
import "../../styles/admin.css";

interface CoverageResp {
  total_active: number;
  with_any_social: number;
  by_source: { source: string; n: number; flagged: number }[];
  by_platform: { platform: string; n: number; flagged: number }[];
}

interface FlaggedRow {
  id: string;
  politician_id: string;
  platform: string;
  handle: string | null;
  url: string;
  source: string | null;
  confidence: number | null;
  evidence_url: string | null;
  discovered_at: string | null;
  politician_name: string;
  level: string;
  province_territory: string | null;
  party: string | null;
  constituency_name: string | null;
}

interface FlaggedResp { items: FlaggedRow[] }

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="admin__stat">
      <div className="admin__stat-value">{value}</div>
      <div className="admin__stat-label">{label}</div>
      {sub && <div className="admin__stat-sub">{sub}</div>}
    </div>
  );
}

export default function AdminSocialsReview() {
  useDocumentTitle("Admin · Socials Review");
  const [platformFilter, setPlatformFilter] = useState<string>("");
  const [busy, setBusy] = useState<string | null>(null);

  const cov = useAdminFetch<CoverageResp>("/socials/coverage", { pollMs: 10000 });
  const path = platformFilter
    ? `/socials/flagged?platform=${encodeURIComponent(platformFilter)}&limit=100`
    : "/socials/flagged?limit=100";
  const flagged = useAdminFetch<FlaggedResp>(path);

  async function approve(id: string) {
    setBusy(id);
    try {
      await adminFetch(`/socials/${id}/approve`, { method: "POST" });
      flagged.refresh();
      cov.refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Approve failed");
    } finally {
      setBusy(null);
    }
  }

  async function reject(id: string) {
    if (!confirm("Delete this social handle row? This cannot be undone.")) return;
    setBusy(id);
    try {
      await adminFetch(`/socials/${id}/reject`, { method: "POST" });
      flagged.refresh();
      cov.refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Reject failed");
    } finally {
      setBusy(null);
    }
  }

  const items = flagged.data?.items ?? [];
  const coverage = cov.data;

  return (
    <div className="admin__content">
      {coverage && (
        <div className="admin__stats-grid">
          <Stat
            label="Active politicians"
            value={coverage.total_active.toLocaleString()}
          />
          <Stat
            label="With ≥1 social"
            value={coverage.with_any_social.toLocaleString()}
            sub={coverage.total_active > 0
              ? `${Math.round(100 * coverage.with_any_social / coverage.total_active)}% coverage`
              : undefined}
          />
          <Stat
            label="Flagged for review"
            value={
              coverage.by_platform
                .reduce((acc, r) => acc + (r.flagged || 0), 0)
                .toLocaleString()
            }
            sub="pattern_probe + agent_sonnet below threshold"
          />
        </div>
      )}

      {coverage && (
        <section className="admin__section">
          <h3>Rows by source</h3>
          <table className="admin__table">
            <thead>
              <tr><th>Source</th><th>Rows</th><th>Flagged</th></tr>
            </thead>
            <tbody>
              {coverage.by_source.map(s => (
                <tr key={s.source}>
                  <td><code>{s.source}</code></td>
                  <td>{s.n.toLocaleString()}</td>
                  <td>{s.flagged}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="admin__section">
        <div className="admin__toolbar">
          <label>
            Platform:{" "}
            <select value={platformFilter} onChange={e => setPlatformFilter(e.target.value)}>
              <option value="">All</option>
              {coverage?.by_platform.map(p => (
                <option key={p.platform} value={p.platform}>
                  {p.platform} ({p.flagged} flagged)
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => flagged.refresh()}>Refresh</button>
        </div>

        {flagged.loading && !flagged.data && (
          <p className="admin__empty">Loading flagged rows…</p>
        )}
        {items.length === 0 && !flagged.loading && (
          <p className="admin__empty">
            No flagged rows{platformFilter ? ` for ${platformFilter}` : ""} — queue is clear.
          </p>
        )}

        {items.length > 0 && (
          <table className="admin__table">
            <thead>
              <tr>
                <th>Politician</th>
                <th>Platform</th>
                <th>URL</th>
                <th>Conf</th>
                <th>Source</th>
                <th>Evidence</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map(r => (
                <tr key={r.id}>
                  <td>
                    <strong>{r.politician_name}</strong>
                    <div className="admin__stat-sub">
                      {r.level}
                      {r.province_territory && ` · ${r.province_territory}`}
                      {r.constituency_name && ` · ${r.constituency_name}`}
                      {r.party && ` · ${r.party}`}
                    </div>
                  </td>
                  <td><code>{r.platform}</code></td>
                  <td>
                    {safeHttpHref(r.url) ? (
                      <a href={safeHttpHref(r.url)} target="_blank" rel="noreferrer">
                        {r.handle || r.url}
                      </a>
                    ) : (
                      <code title="non-http(s) URL — not rendered as a link">{r.handle || r.url}</code>
                    )}
                  </td>
                  <td>{r.confidence == null ? "—" : Number(r.confidence).toFixed(2)}</td>
                  <td><code>{r.source || "—"}</code></td>
                  <td>
                    {safeHttpHref(r.evidence_url) ? (
                      <a href={safeHttpHref(r.evidence_url)} target="_blank" rel="noreferrer">
                        ↗ link
                      </a>
                    ) : r.evidence_url ? (
                      <code title="non-http(s) URL — not rendered as a link">{r.evidence_url}</code>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <button
                      disabled={busy === r.id}
                      onClick={() => approve(r.id)}
                    >
                      Approve
                    </button>
                    {" "}
                    <button
                      disabled={busy === r.id}
                      onClick={() => reject(r.id)}
                      className="admin__btn-danger"
                    >
                      Reject
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
