import { TIER_META, type SovereigntyTier } from "../types";

/**
 * Map key — explains every glyph the user might see on the map:
 * tier colors, polygon vs pin vs line styles, and the difference between
 * the static (Canadian) and animated (foreign) connection lines.
 */
export function TierLegend() {
  const tiers: SovereigntyTier[] = [1, 2, 3, 4, 5, 6];
  return (
    <div className="tier-legend">
      <div className="tier-legend__section">
        <div className="tier-legend__title">Sovereignty tier (color)</div>
        <div className="tier-legend__row">
          {tiers.map(t => (
            <span key={t} className="tier-legend__item">
              <span className="tier-legend__swatch" style={{ background: TIER_META[t].color }} />
              <span>{TIER_META[t].emoji} {t} · {TIER_META[t].label}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="tier-legend__section">
        <div className="tier-legend__title">What's on the map</div>
        <div className="tier-legend__row">
          <span className="tier-legend__item">
            <svg width="22" height="14" viewBox="0 0 22 14" aria-hidden>
              <rect x="1" y="1" width="20" height="12" rx="2"
                    fill="#dc2626" fillOpacity="0.25" stroke="#dc2626" strokeWidth="0.8"/>
            </svg>
            <span>Constituency · filled by its worst-tier site</span>
          </span>

          <span className="tier-legend__item">
            <svg width="22" height="14" viewBox="0 0 22 14" aria-hidden>
              <rect x="1" y="1" width="20" height="12" rx="2"
                    fill="#1e293b" stroke="#475569" strokeWidth="0.8" strokeDasharray="3 3"/>
            </svg>
            <span>Riding without a tracked website</span>
          </span>

          <span className="tier-legend__item">
            <svg width="22" height="14" viewBox="0 0 22 14" aria-hidden>
              <circle cx="11" cy="7" r="5" fill="#6366f1" stroke="#0b1220" strokeWidth="1"/>
            </svg>
            <span>Server location · pin colored by tier</span>
          </span>

          <span className="tier-legend__item">
            <svg width="40" height="10" viewBox="0 0 40 10" aria-hidden>
              <line x1="0" y1="5" x2="40" y2="5" stroke="#ea580c" strokeWidth="1.4" opacity="0.5"/>
            </svg>
            <span>Static line · website hosted in Canada</span>
          </span>

          <span className="tier-legend__item">
            <svg width="40" height="10" viewBox="0 0 40 10" aria-hidden className="tier-legend__ant">
              <line x1="0" y1="5" x2="40" y2="5" stroke="#6366f1" strokeWidth="1.6"
                    strokeDasharray="6 6" opacity="0.85"/>
            </svg>
            <span>Animated line · data flowing outside Canada</span>
          </span>
        </div>
      </div>
    </div>
  );
}
