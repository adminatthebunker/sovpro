import { TIER_META, type SovereigntyTier } from "../types";

interface Props {
  /** When provided, each tier row renders as a checkbox that toggles
   *  the tier's visibility on the map. When omitted, renders a static
   *  key (original behaviour). */
  visibleTiers?: Set<SovereigntyTier>;
  onToggleTier?: (tier: SovereigntyTier) => void;
  onSetAllTiers?: (checked: boolean) => void;
}

/**
 * Map key — explains every glyph the user might see on the map:
 * tier colors, polygon vs pin vs line styles, and the difference between
 * the static (Canadian) and animated (foreign) connection lines.
 *
 * When `visibleTiers` + `onToggleTier` are supplied, the tier row becomes
 * interactive: unchecking a tier hides every feature of that tier from
 * the map (constituencies, server pins, and connection lines).
 */
export function TierLegend({ visibleTiers, onToggleTier, onSetAllTiers }: Props = {}) {
  const tiers: SovereigntyTier[] = [1, 2, 3, 4, 5, 6];
  const interactive = !!visibleTiers && !!onToggleTier;
  const allOn = interactive && tiers.every(t => visibleTiers!.has(t));
  const noneOn = interactive && tiers.every(t => !visibleTiers!.has(t));

  return (
    <div className="tier-legend">
      <div className="tier-legend__section">
        <div className="tier-legend__title">
          <span>Sovereignty tier (color)</span>
          {interactive && onSetAllTiers && (
            <span className="tier-legend__bulk">
              <button
                type="button"
                className="tier-legend__bulk-btn"
                onClick={() => onSetAllTiers(true)}
                disabled={allOn}
              >Show all</button>
              <button
                type="button"
                className="tier-legend__bulk-btn"
                onClick={() => onSetAllTiers(false)}
                disabled={noneOn}
              >Hide all</button>
            </span>
          )}
        </div>
        <div className="tier-legend__row">
          {tiers.map(t => {
            const meta = TIER_META[t];
            const label = (
              <>
                <span className="tier-legend__swatch" style={{ background: meta.color }} />
                <span>{meta.emoji} {t} · {meta.label}</span>
              </>
            );
            if (interactive) {
              const checked = visibleTiers!.has(t);
              return (
                <label
                  key={t}
                  className={`tier-legend__item tier-legend__item--check ${checked ? "" : "is-off"}`}
                  title={`Toggle tier ${t} · ${meta.label}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggleTier!(t)}
                  />
                  {label}
                </label>
              );
            }
            return (
              <span key={t} className="tier-legend__item">
                {label}
              </span>
            );
          })}
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
            <span>Server · personal site (their own choice)</span>
          </span>

          <span className="tier-legend__item">
            <svg width="22" height="14" viewBox="0 0 22 14" aria-hidden>
              <circle cx="11" cy="7" r="5.5" fill="transparent" stroke="#6366f1" strokeWidth="2"/>
            </svg>
            <span>Server · party-managed subdomain</span>
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
