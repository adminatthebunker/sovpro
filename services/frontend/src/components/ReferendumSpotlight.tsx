import { useFetch } from "../hooks/useFetch";
import type { ReferendumSideSummary } from "../types";
import { TIER_META, type SovereigntyTier } from "../types";

interface RefResponse {
  leave_side: ReferendumSideSummary;
  stay_side: ReferendumSideSummary;
  irony_score: string;
}

export function ReferendumSpotlight() {
  const { data, loading, error } = useFetch<RefResponse>("/stats/referendum");

  if (loading) return <div className="ref">Loading referendum data…</div>;
  if (error)   return <div className="ref ref--error">{error.message}</div>;
  if (!data)   return null;

  return (
    <section className="ref">
      <header className="ref__header">
        <h2>🗳️ Referendum Watch — October 19, 2026</h2>
        <p>{data.irony_score || "Scanning both sides of Alberta's sovereignty debate."}</p>
      </header>

      <div className="ref__grid">
        <SideCard side="leave" title="Leave side" data={data.leave_side} />
        <SideCard side="stay"  title="Stay side"  data={data.stay_side} />
      </div>

      <p className="ref__footnote">
        Neither side of Alberta's sovereignty debate hosts their digital infrastructure in Canada.
      </p>
    </section>
  );
}

function SideCard({ side, title, data }: { side: "leave" | "stay"; title: string; data: ReferendumSideSummary }) {
  const className = `side-card side-card--${side}`;
  return (
    <div className={className}>
      <h3>{title}</h3>
      <div className="side-card__summary">
        <div>
          <span className="side-card__big">{data.hosted_in_canada}</span>
          <span>/{data.total_websites}</span>
          <p>websites in Canada</p>
        </div>
        <div>
          <span className="side-card__big">{data.hosted_in_us}</span>
          <span>/{data.total_websites}</span>
          <p>websites in the US</p>
        </div>
      </div>

      <ul className="side-card__orgs">
        {(data.websites ?? []).map((w, i) => (
          <li key={i}>
            <div className="side-card__org">{w.org_name}</div>
            <div className="side-card__url"><a href={w.website_url} target="_blank" rel="noopener">{w.hostname}</a></div>
            <div className="side-card__meta">
              {TIER_META[(w.sovereignty_tier ?? 6) as SovereigntyTier].emoji} {TIER_META[(w.sovereignty_tier ?? 6) as SovereigntyTier].label}
              {" · "}{w.hosting_provider ?? "unknown"}
              {w.ip_city ? ` · ${w.ip_city}` : ""}
              {w.ip_country ? ` ${w.ip_country}` : ""}
            </div>
          </li>
        ))}
      </ul>

      {data.providers.length > 0 && (
        <div className="side-card__providers">
          Providers: {data.providers.join(", ")}
        </div>
      )}
    </div>
  );
}
