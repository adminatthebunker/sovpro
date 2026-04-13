import { useFetch } from "../hooks/useFetch";

interface StatsRollup {
  politicians: {
    total: number;
    by_level: Record<string, number>;
  };
  organizations: { total: number };
}

interface AlbertaCoverageRow {
  level: string;
  set_name: string | null;
  politicians: number;
}

interface AlbertaOverview {
  coverage: AlbertaCoverageRow[];
}

/**
 * Compact panel that itemizes every group SovereignWatch is tracking.
 * Sits beside the hero so visitors immediately see "of whom" the headline
 * percentage applies to.
 */
export function WhoWeTrack() {
  const { data: stats } = useFetch<StatsRollup>("/stats");
  const { data: ab } = useFetch<AlbertaOverview>("/alberta/overview");

  const fed = stats?.politicians.by_level.federal ?? 0;
  const prov = stats?.politicians.by_level.provincial ?? 0;
  const muni = stats?.politicians.by_level.municipal ?? 0;
  const orgs = stats?.organizations.total ?? 0;

  // City-by-city breakdown for AB municipal
  const muniRows = (ab?.coverage ?? []).filter(c => c.level === "municipal");
  const cities = muniRows
    .map(c => ({
      label: (c.set_name ?? "")
        .replace(" City Council", "")
        .replace(" Municipal Council", "")
        .replace(" Council", "")
        .trim(),
      n: c.politicians,
    }))
    .filter(c => c.label)
    .sort((a, b) => b.n - a.n);

  return (
    <aside className="who-we-track" aria-label="Who we track">
      <header>
        <span className="who-we-track__eyebrow">Who we track</span>
        <h3>{fed + prov + muni + orgs} entities scanned</h3>
      </header>
      <ul className="who-we-track__list">
        <li>
          <span className="who-we-track__n">{fed}</span>
          <span>Federal Members of Parliament</span>
        </li>
        <li>
          <span className="who-we-track__n">{prov}</span>
          <span>Alberta MLAs</span>
        </li>
        <li>
          <span className="who-we-track__n">{muni}</span>
          <span>Alberta councillors &amp; mayors</span>
          {cities.length > 0 && (
            <ul className="who-we-track__sub">
              {cities.map(c => (
                <li key={c.label}>
                  {c.label} <span>({c.n})</span>
                </li>
              ))}
            </ul>
          )}
        </li>
        <li>
          <span className="who-we-track__n">{orgs}</span>
          <span>Political parties &amp; referendum organizations</span>
        </li>
      </ul>
      <footer className="who-we-track__foot">
        Data from <a href="https://represent.opennorth.ca" target="_blank" rel="noopener noreferrer">Open North</a>.
        Personal/campaign sites discovered via web search.
      </footer>
    </aside>
  );
}
