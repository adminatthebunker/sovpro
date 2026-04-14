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

  // Denominator behind the headline: politicians with ≥1 sovereignty-tiered
  // website (i.e. actually scanned). Derived from tier buckets so it stays
  // live as the scanner backfills new rows.
  const scanned = data
    ? Object.values(data.politicians.sovereignty ?? {}).reduce((n, v) => n + (v as number), 0)
    : 0;
  const total = data?.politicians.total ?? 0;
  const coverage = total > 0 ? Math.round(100 * scanned / total) : 0;

  return (
    <section className="hero">
      {usPct !== null && (
        <>
          <h2 className="hero__headline">
            <span className="hero__number">{usPct}%</span> of the{" "}
            <strong>{scanned.toLocaleString()}</strong> Canadian politicians whose
            websites we&apos;ve analyzed host <strong>outside Canada</strong>.
          </h2>
          {total > scanned && (
            <p className="hero__coverage" title={`${scanned} of ${total} tracked politicians scanned so far`}>
              Scan coverage: {coverage}% ({scanned.toLocaleString()} of {total.toLocaleString()}). More data landing continuously.
            </p>
          )}
        </>
      )}
      {ref?.irony_score && (
        <p className="hero__subhead">{ref.irony_score}</p>
      )}
    </section>
  );
}
