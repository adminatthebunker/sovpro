import { useFetch } from "../hooks/useFetch";
import type { StatsResponse } from "../types";
import { COUNTRY_FLAGS } from "../types";

export function StatsBar() {
  const { data } = useFetch<StatsResponse>("/stats");

  if (!data) return <div className="statsbar statsbar--loading">Loading stats…</div>;

  const notCanadian = data.politicians.pct_not_canadian;
  const tier1 = data.politicians.sovereignty?.tier_1 ?? 0;
  const totalPoliticians = data.politicians.total;
  const topForeign = data.top_foreign_locations?.[0];

  return (
    <div className="statsbar">
      <Stat value={`${notCanadian}%`} label="of politicians' websites are hosted outside Canada" />
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

function Stat({ value, sub, label }: { value: string; sub?: string; label: string }) {
  return (
    <div className="statcard">
      <div className="statcard__value">{value}{sub ? <span className="statcard__sub"> {sub}</span> : null}</div>
      <div className="statcard__label">{label}</div>
    </div>
  );
}
