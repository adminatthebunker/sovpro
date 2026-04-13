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

  return (
    <section className="hero">
      {usPct !== null && (
        <h2 className="hero__headline">
          <span className="hero__number">{usPct}%</span> of tracked Canadian politicians host their websites <strong>outside Canada</strong>.
        </h2>
      )}
      {ref?.irony_score && (
        <p className="hero__subhead">{ref.irony_score}</p>
      )}
    </section>
  );
}
