import { useEffect } from "react";
import { useFetch } from "../hooks/useFetch";
import { COUNTRY_FLAGS, TIER_META, type SovereigntyTier } from "../types";

interface ReportData {
  party: string;
  grade: "A" | "B" | "C" | "D" | "F";
  grade_class: "a" | "b" | "c" | "d" | "f";
  pct_canadian: number;
  politicians: number;
  sites: number;
  personal_sites: number;
  party_managed_sites: number;
  no_website: number;
  breakdown: { canadian: number; cdn: number; us: number; foreign: number };
  top_providers: Array<{ provider: string; n: number }>;
  top_foreign_locations: Array<{ city: string; country: string; n: number }>;
  best_mps: Array<{ name: string; constituency_name: string | null; tier: number }>;
  worst_mps: Array<{ name: string; constituency_name: string | null; tier: number; provider: string | null; city: string | null; country: string | null }>;
}

interface Props {
  party: string;
  partyColor: string;
  onClose: () => void;
  /** Layout variant. 'drawer' is inline-beside-map (default).
   *  'modal' overlays the page with a backdrop. */
  variant?: "drawer" | "modal";
}

export function PartyReportCard({ party, partyColor, onClose, variant = "drawer" }: Props) {
  const path = `/parties/${encodeURIComponent(party)}/report`;
  const { data, loading, error } = useFetch<ReportData>(path);

  // Esc to close
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const inner = (
    <>
      <button className="report-card__close" onClick={onClose} aria-label="Close">×</button>
      {loading && <div className="report-card__loading">Loading {party} report card…</div>}
      {error && <div className="report-card__error">{error.message}</div>}
      {data && <ReportBody data={data} partyColor={partyColor} />}
    </>
  );

  if (variant === "modal") {
    return (
      <div className="report-modal" role="dialog" aria-modal="true" onClick={onClose}>
        <div className="report-card" onClick={e => e.stopPropagation()}>
          {inner}
        </div>
      </div>
    );
  }

  // drawer: inline beside the map
  return (
    <aside className="report-drawer" role="complementary" aria-label={`${party} sovereignty report card`}>
      <div className="report-card report-card--drawer">
        {inner}
      </div>
    </aside>
  );
}

function ReportBody({ data, partyColor }: { data: ReportData; partyColor: string }) {
  const totalNotCanadian = data.breakdown.cdn + data.breakdown.us + data.breakdown.foreign;
  const pctNotCanadian = data.sites > 0 ? Math.round(100 * totalNotCanadian / data.sites) : 0;
  const usPct = data.sites > 0 ? Math.round(100 * data.breakdown.us / data.sites) : 0;
  const cdnPct = data.sites > 0 ? Math.round(100 * data.breakdown.cdn / data.sites) : 0;
  const partyManagedPct = data.sites > 0 ? Math.round(100 * data.party_managed_sites / data.sites) : 0;

  return (
    <>
      <header className="report-card__head" style={{ borderTopColor: partyColor }}>
        <div className="report-card__title">
          <div className="report-card__eyebrow">SOVEREIGNTY REPORT CARD</div>
          <h2 style={{ color: partyColor }}>{data.party}</h2>
          <div className="report-card__count">
            {data.politicians} elected {data.politicians === 1 ? "official" : "officials"} tracked
          </div>
        </div>
        <div className={`report-card__grade report-card__grade--${data.grade_class}`}>
          <div className="report-card__grade-letter">{data.grade}</div>
          <div className="report-card__grade-pct">{data.pct_canadian}% in Canada</div>
        </div>
      </header>

      <section className="report-card__bars">
        <Bar label="🍁 Canadian (tiers 1+2)" value={data.breakdown.canadian} total={data.sites} color="#22c55e" />
        <Bar label="🌐 CDN-fronted (US infrastructure)" value={data.breakdown.cdn} total={data.sites} color="#0891b2" />
        <Bar label="🇺🇸 US-hosted directly" value={data.breakdown.us} total={data.sites} color="#6366f1" />
        {data.breakdown.foreign > 0 && (
          <Bar label="🌍 Other foreign" value={data.breakdown.foreign} total={data.sites} color="#a855f7" />
        )}
      </section>

      <section className="report-card__keystats">
        <KeyStat n={pctNotCanadian + "%"} label="of party sites are hosted outside Canada" />
        <KeyStat n={data.personal_sites + ""} label="personal/campaign sites" />
        <KeyStat n={data.party_managed_sites + ""}
                 label={`party-managed subdomains (${partyManagedPct}%)`} />
        <KeyStat n={data.no_website + ""} label="elected officials without a tracked site" />
      </section>

      {data.top_providers.length > 0 && (
        <section className="report-card__section">
          <h3>Top hosting providers</h3>
          <ul className="report-card__chips">
            {data.top_providers.map(p => (
              <li key={p.provider} className="report-card__chip">
                {p.provider} <span>{p.n}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.top_foreign_locations.length > 0 && (
        <section className="report-card__section">
          <h3>Top destinations outside Canada</h3>
          <ul className="report-card__chips">
            {data.top_foreign_locations.map((c, i) => (
              <li key={i} className="report-card__chip">
                {COUNTRY_FLAGS[c.country] ?? ""} {c.city}, {c.country} <span>{c.n}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="report-card__split">
        <div className="report-card__split-side report-card__split-side--good">
          <h3>🏆 Best in class</h3>
          {data.best_mps.length === 0 ? (
            <p className="report-card__muted">No politicians with a Canadian-hosted site.</p>
          ) : (
            <ul className="report-card__mps">
              {data.best_mps.map((mp, i) => {
                const meta = TIER_META[mp.tier as SovereigntyTier];
                return (
                  <li key={i}>
                    <div>{mp.name}</div>
                    <div className="report-card__mp-meta">
                      {mp.constituency_name && <span>{mp.constituency_name} · </span>}
                      <span style={{ color: meta.color }}>{meta.emoji} {meta.label}</span>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className="report-card__split-side report-card__split-side--bad">
          <h3>📉 Furthest from sovereignty</h3>
          {data.worst_mps.length === 0 ? (
            <p className="report-card__muted">All party sites are at least CDN-fronted or better.</p>
          ) : (
            <ul className="report-card__mps">
              {data.worst_mps.map((mp, i) => {
                const meta = TIER_META[mp.tier as SovereigntyTier];
                const loc = [mp.city, mp.country].filter(Boolean).join(", ");
                return (
                  <li key={i}>
                    <div>{mp.name}</div>
                    <div className="report-card__mp-meta">
                      {mp.constituency_name && <span>{mp.constituency_name} · </span>}
                      <span style={{ color: meta.color }}>{meta.emoji} {meta.label}</span>
                      {mp.provider && <span> · {mp.provider}</span>}
                      {loc && <span> · {loc}</span>}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>

      <footer className="report-card__foot">
        <div className="report-card__methodology">
          <strong>Grading scale:</strong> A ≥85% in Canada · B ≥70% · C ≥50% · D ≥30% · F &lt;30%.
          Sites deduplicated by hostname; shared parliamentary infrastructure
          (ourcommons.ca, assembly.ab.ca) is excluded. CDN-fronted sites count
          as outside Canada because the CDN itself is US-headquartered and
          subject to US law.
        </div>
      </footer>
    </>
  );
}

function Bar({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
  const pct = total > 0 ? (100 * value) / total : 0;
  return (
    <div className="report-card__bar">
      <div className="report-card__bar-label">
        <span>{label}</span>
        <span>{value} ({Math.round(pct)}%)</span>
      </div>
      <div className="report-card__bar-track">
        <div className="report-card__bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function KeyStat({ n, label }: { n: string; label: string }) {
  return (
    <div className="report-card__keystat">
      <div className="report-card__keystat-num">{n}</div>
      <div className="report-card__keystat-label">{label}</div>
    </div>
  );
}
