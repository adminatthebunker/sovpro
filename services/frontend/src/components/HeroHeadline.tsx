import { useFetch } from "../hooks/useFetch";
import type { StatsResponse } from "../types";

export function HeroHeadline() {
  const { data } = useFetch<StatsResponse>("/stats");
  const { data: ref } = useFetch<{
    leave_side: { total_websites: number; hosted_in_us: number; hosted_in_canada: number; orgs: string[] };
    stay_side:  { total_websites: number; hosted_in_us: number; hosted_in_canada: number; orgs: string[] };
    irony_score: string;
  }>("/stats/referendum");

  const usPct = data ? Math.round(data.politicians.pct_not_canadian) : null;
  // Headline: "X of Y tracked Canadian politicians host their data in Canada."
  const tier = data?.politicians?.sovereignty ?? {};
  const totalScored =
    (tier.tier_1 ?? 0) + (tier.tier_2 ?? 0) +
    (tier.tier_3 ?? 0) + (tier.tier_4 ?? 0) +
    (tier.tier_5 ?? 0);
  const inCanada = (tier.tier_1 ?? 0) + (tier.tier_2 ?? 0);

  return (
    <section className="hero">
      {totalScored > 0 && (
        <h2 className="hero__killer">
          <span className="hero__killer-num">{inCanada}</span>
          <span className="hero__killer-of">of</span>
          <span className="hero__killer-denom">{totalScored}</span>
          <span className="hero__killer-label">
            tracked Canadian-politician websites are physically hosted in Canada.
          </span>
        </h2>
      )}
      {usPct !== null && (
        <p className="hero__subhead">
          <strong>{usPct}%</strong> are hosted outside Canada entirely.{ref?.irony_score ? ` ${ref.irony_score}` : ""}
        </p>
      )}
    </section>
  );
}
