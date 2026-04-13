import { useFetch } from "../hooks/useFetch";
import type { ChangeItem } from "../types";
import { ChangeRow } from "./ChangeRow";

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
        {items.map(c => <ChangeRow key={c.id} change={c} />)}
      </ol>

      <aside className="changes__verify">
        <h3>Self-verify any claim above</h3>
        <p>Don't trust us — check it yourself in 10 seconds.</p>
        <pre>{`# Resolve the IP
dig +short <hostname>

# Look up who owns it
whois $(dig +short <hostname> | head -1)

# Or use a no-install web tool:
https://hackertarget.com/ip-tools/
https://viewdns.info/iplocation/?ip=<ip>
https://ipinfo.io/<ip>`}</pre>
      </aside>
    </section>
  );
}
