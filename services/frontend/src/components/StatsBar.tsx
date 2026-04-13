import { useFetch } from "../hooks/useFetch";
import type { StatsResponse } from "../types";

export function StatsBar() {
  const { data } = useFetch<StatsResponse>("/stats");

  if (!data) return <div className="statsbar statsbar--loading">Loading stats…</div>;

  const notCanadian = data.politicians.pct_not_canadian;
  const topCity = data.top_server_locations[0];
  const tier1 = data.politicians.sovereignty?.tier_1 ?? 0;
  const totalPoliticians = data.politicians.total;

  return (
    <div className="statsbar">
      <Stat value={`${notCanadian}%`} label="of politicians' websites are hosted outside Canada" />
      <Stat value={String(totalPoliticians)} label="politicians tracked" />
      <Stat value={String(tier1)} label="use Canadian-owned hosting" />
      {topCity && (
        <Stat
          value={topCity.city}
          sub={topCity.country}
          label={`most popular location for Canadian political data (${topCity.n} sites)`}
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
