import { TIER_META, type SovereigntyTier } from "../types";

export function TierLegend() {
  const tiers: SovereigntyTier[] = [1, 2, 3, 4, 5, 6];
  return (
    <div className="tier-legend">
      <span className="tier-legend__title">Sovereignty tiers</span>
      {tiers.map(t => (
        <span key={t} className="tier-legend__item">
          <span className="tier-legend__swatch" style={{ background: TIER_META[t].color }} />
          <span>{TIER_META[t].emoji} {t} · {TIER_META[t].label}</span>
        </span>
      ))}
    </div>
  );
}
