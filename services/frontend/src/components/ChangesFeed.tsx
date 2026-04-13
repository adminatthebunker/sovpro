import { useFetch } from "../hooks/useFetch";
import type { ChangeItem } from "../types";

interface ChangesResp {
  items: ChangeItem[];
  page: number;
  total: number;
}

export function ChangesFeed() {
  const { data, loading, error, refresh } = useFetch<ChangesResp>("/changes?limit=50");

  if (loading) return <div className="changes">Loading changes…</div>;
  if (error)   return <div className="changes changes--error">{error.message}</div>;

  const items = data?.items ?? [];

  return (
    <section className="changes">
      <header className="changes__header">
        <h2>Infrastructure changes</h2>
        <button onClick={refresh}>Refresh</button>
      </header>
      {items.length === 0 && <p>No changes recorded yet — changes appear here after the scanner detects differences between scans.</p>}
      <ol className="changes__list">
        {items.map(c => (
          <li key={c.id} className={`changes__row changes__row--${c.severity}`}>
            <div className="changes__time">{new Date(c.detected_at).toLocaleString()}</div>
            <div className="changes__summary">
              <strong>{c.owner_name}</strong>
              <a href={c.website_url} target="_blank" rel="noopener">{c.website_url}</a>
              <span className={`changes__type changes__type--${c.change_type}`}>{c.change_type.replace(/_/g, " ")}</span>
              <p>{c.summary}</p>
              {(c.old_value || c.new_value) && (
                <div className="changes__diff">
                  <del>{c.old_value ?? "—"}</del>
                  <span> → </span>
                  <ins>{c.new_value ?? "—"}</ins>
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
