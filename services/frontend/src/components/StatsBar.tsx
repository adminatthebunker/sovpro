import { useFetch } from "../hooks/useFetch";
import type { StatsResponse } from "../types";
import { COUNTRY_FLAGS } from "../types";

export function StatsBar() {
  const { data } = useFetch<StatsResponse>("/stats");

  if (!data) return <div className="statsbar statsbar--loading">Loading stats…</div>;

  const tier1 = data.politicians.sovereignty?.tier_1 ?? 0;
  const totalPoliticians = data.politicians.total;
  const topForeign = data.top_foreign_locations?.[0];

  // Concentration: top 3 hosting providers' combined share of all unique sites.
  // sovereignty totals are already dedup'd-by-hostname and exclude shared_official —
  // same denominator as top_providers, so the math is consistent.
  const tierCounts = data.politicians.sovereignty ?? {};
  const totalSites =
    (tierCounts.tier_1 ?? 0) + (tierCounts.tier_2 ?? 0) +
    (tierCounts.tier_3 ?? 0) + (tierCounts.tier_4 ?? 0) +
    (tierCounts.tier_5 ?? 0);
  const top3 = (data.top_providers ?? []).slice(0, 3);
  const top3Sum = top3.reduce((n, p) => n + p.n, 0);
  const top3Pct = totalSites > 0 ? Math.round(100 * top3Sum / totalSites) : 0;
  const top3Names = top3.map(p => p.provider).join(" + ");

  return (
    <div className="statsbar">
      <Stat
        value={String(top3.length)}
        label={`hosting companies hold ${top3Pct}% of Canadian political web data`}
        title={top3Names ? `${top3Names} together host ${top3Sum} of ${totalSites} unique sites` : undefined}
      />
      <Stat value={String(totalPoliticians)} label="politicians tracked" />
      <Stat value={String(tier1)} label="use Canadian-owned hosting" />
      {topForeign && (
        <Stat
          value={topForeign.city}
          sub={`${COUNTRY_FLAGS[topForeign.country] ?? ""} ${topForeign.country}`}
          label={`top destination outside Canada (${topForeign.n} sites)`}
        />
      )}
    </div>
  );
}

function Stat({ value, sub, label, title }: { value: string; sub?: string; label: string; title?: string }) {
  return (
    <div className="statcard" title={title}>
      <div className="statcard__value">{value}{sub ? <span className="statcard__sub"> {sub}</span> : null}</div>
      <div className="statcard__label">{label}</div>
    </div>
  );
}
