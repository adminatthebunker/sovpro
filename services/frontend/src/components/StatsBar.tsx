import { useFetch } from "../hooks/useFetch";
import type { StatsResponse } from "../types";
import { COUNTRY_FLAGS } from "../types";

export function StatsBar() {
  const { data } = useFetch<StatsResponse>("/stats");

  if (!data) return <div className="statsbar statsbar--loading">Loading stats…</div>;

  const tier1 = data.politicians.sovereignty?.tier_1 ?? 0;
  const tier2 = data.politicians.sovereignty?.tier_2 ?? 0;
  const canadianSoil = tier1 + tier2;
  const topForeign = data.top_foreign_locations?.[0];
  const offices = data.dataset_depth?.offices_mapped ?? 0;
  const committees = data.dataset_depth?.committees_tracked ?? 0;

  const tierCounts = data.politicians.sovereignty ?? {};
  const totalSites =
    (tierCounts.tier_1 ?? 0) + (tierCounts.tier_2 ?? 0) +
    (tierCounts.tier_3 ?? 0) + (tierCounts.tier_4 ?? 0) +
    (tierCounts.tier_5 ?? 0);
  const top3 = (data.top_providers ?? []).slice(0, 3);
  const top3Sum = top3.reduce((n, p) => n + p.n, 0);
  const top3Pct = totalSites > 0 ? Math.round(100 * top3Sum / totalSites) : 0;
  const top3Names = top3.map(p => p.provider).join(" + ");

  const socialsTotal = data.socials_adoption?.total_with_any ?? 0;
  const socialsAll   = socialsTotal + (data.socials_adoption?.total_without ?? 0);
  const socialsPct   = socialsAll > 0 ? Math.round(100 * socialsTotal / socialsAll) : 0;

  return (
    <div className="statsbar">
      <Stat
        accent="warn"
        icon="🏢"
        value={String(top3.length)}
        label={`hosting companies hold ${top3Pct}% of Canadian political web data`}
        title={top3Names ? `${top3Names} together host ${top3Sum} of ${totalSites} unique sites` : undefined}
      />
      <Stat
        accent="info"
        icon="📍"
        value={offices.toLocaleString()}
        label="constituency & legislature offices mapped"
        title={committees > 0 ? `Plus ${committees.toLocaleString()} committee memberships tracked` : undefined}
      />
      <Stat
        accent="good"
        icon="🍁"
        value={String(canadianSoil)}
        label="host on Canadian soil"
        title={`${tier1} truly sovereign (Canadian-owned) · ${tier2} Canadian soil via foreign providers (AWS / Azure / Shopify etc.)`}
      />
      {socialsAll > 0 && (
        <Stat
          accent="info"
          icon="📱"
          value={`${socialsPct}%`}
          label="have a public social-media presence"
          title={`${socialsTotal} of ${socialsAll} politicians linked to ≥1 handle`}
        />
      )}
      {topForeign && (
        <Stat
          accent="bad"
          icon={COUNTRY_FLAGS[topForeign.country] ?? "🌐"}
          value={topForeign.city}
          sub={topForeign.country}
          label={`top destination outside Canada (${topForeign.n} sites)`}
        />
      )}
    </div>
  );
}

interface StatProps {
  value: string;
  sub?: string;
  label: string;
  title?: string;
  icon?: string;
  accent?: "good" | "bad" | "warn" | "info";
}

function Stat({ value, sub, label, title, icon, accent = "info" }: StatProps) {
  return (
    <div className={`statcard statcard--${accent}`} title={title}>
      <div className="statcard__rail" aria-hidden />
      {icon && <div className="statcard__icon" aria-hidden>{icon}</div>}
      <div className="statcard__body">
        <div className="statcard__value">
          {value}
          {sub ? <span className="statcard__sub"> {sub}</span> : null}
        </div>
        <div className="statcard__label">{label}</div>
      </div>
    </div>
  );
}
