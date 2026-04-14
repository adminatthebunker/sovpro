import { useFetch } from "../hooks/useFetch";

interface StatsRollup {
  politicians: {
    total: number;
    by_level: Record<string, number>;
  };
  politicians_by_province?: Record<string, number>;
  socials_adoption?: {
    total_with_any: number;
    total_without: number;
  };
  organizations: { total: number };
}

const PROVINCE_ORDER: [string, string][] = [
  ["ON", "Ontario"], ["QC", "Québec"], ["BC", "B.C."], ["AB", "Alberta"],
  ["MB", "Manitoba"], ["SK", "Sask."], ["NS", "N.S."], ["NB", "N.B."],
  ["NL", "N.L."], ["PE", "P.E.I."], ["YT", "Yukon"], ["NT", "N.W.T."],
  ["NU", "Nunavut"],
];

/**
 * Compact panel that itemizes every group we track, nationwide.
 * Shows level breakdown + per-province chip row so visitors immediately see
 * the scope of the dataset (13 provinces/territories, all 3 levels of gov).
 */
export function WhoWeTrack() {
  const { data: stats } = useFetch<StatsRollup>("/stats");

  const fed = stats?.politicians.by_level.federal ?? 0;
  const prov = stats?.politicians.by_level.provincial ?? 0;
  const muni = stats?.politicians.by_level.municipal ?? 0;
  const orgs = stats?.organizations.total ?? 0;
  const total = fed + prov + muni + orgs;
  const byProv = stats?.politicians_by_province ?? {};

  const provChips = PROVINCE_ORDER
    .map(([code, label]) => ({ code, label, n: byProv[code] ?? 0 }))
    .filter(p => p.n > 0)
    .sort((a, b) => b.n - a.n);

  const provincesCovered = provChips.length;

  const groups = [
    { icon: "🏛️", n: fed,  label: "Federal (MPs + Senate)", accent: "fed"  },
    { icon: "🌾", n: prov, label: `Provincial · ${provincesCovered} P/Ts`, accent: "prov" },
    { icon: "🏙️", n: muni, label: "Municipal councillors", accent: "muni" },
    { icon: "🗳️", n: orgs, label: "Parties & orgs",         accent: "orgs" },
  ];

  return (
    <aside className="who-we-track" aria-label="Who we track">
      <header className="who-we-track__head">
        <span className="who-we-track__eyebrow">Who we track · nationwide</span>
        <h3>
          <span className="who-we-track__total">{total.toLocaleString()}</span>
          <span className="who-we-track__total-label">politicians &amp; orgs scanned</span>
        </h3>
      </header>

      <div className="who-we-track__grid">
        {groups.map(g => (
          <div key={g.label} className={`wwt-card wwt-card--${g.accent}`}>
            <div className="wwt-card__icon" aria-hidden>{g.icon}</div>
            <div className="wwt-card__body">
              <div className="wwt-card__n">{g.n.toLocaleString()}</div>
              <div className="wwt-card__label">{g.label}</div>
            </div>
          </div>
        ))}
      </div>

      {provChips.length > 0 && (
        <div className="who-we-track__chips">
          <span className="who-we-track__chips-label">Provincial breakdown</span>
          <div className="wwt-chip-row">
            {provChips.map(p => (
              <span key={p.code} className="wwt-chip" title={`${p.n} politicians in ${p.label}`}>
                {p.label} <span className="wwt-chip__n">{p.n}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <footer className="who-we-track__foot">
        Data from <a href="https://represent.opennorth.ca" target="_blank" rel="noopener noreferrer">Open North</a>,
        Wikidata, parl.ca, provincial legislature sites, and council rosters.
        Personal / campaign sites discovered via web scraping.
      </footer>
    </aside>
  );
}
