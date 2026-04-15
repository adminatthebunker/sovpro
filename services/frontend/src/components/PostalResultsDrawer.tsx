import { useEffect } from "react";
import { Link } from "react-router-dom";
import type { PostalLookupResponse, PostalRep } from "./PostalLookupBar";
import { TIER_META, type SovereigntyTier } from "../types";

interface Props {
  data: PostalLookupResponse;
  onClose: () => void;
}

/**
 * Drawer-styled panel rendering postal-code lookup results. Reuses the
 * same `report-drawer` / `report-card--drawer` shell as PartyReportCard so
 * the map + drawer split layout works identically.
 */
export function PostalResultsDrawer({ data, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Aggregate across all reps for the headline summary
  const total = data.representatives.reduce(
    (n, r) => n + (r.scan_summary?.websites ?? 0), 0
  );
  const ca = data.representatives.reduce(
    (n, r) => n + (r.scan_summary?.canadian ?? 0), 0
  );
  const pctOutside = total > 0 ? Math.round(100 * (total - ca) / total) : 0;

  return (
    <aside className="report-drawer" role="complementary" aria-label="Your representatives">
      <div className="report-card report-card--drawer postal-drawer">
        <button className="report-card__close" onClick={onClose} aria-label="Close">×</button>

        <header className="report-card__head postal-drawer__head">
          <div className="report-card__title">
            <div className="report-card__eyebrow">YOUR REPRESENTATIVES</div>
            <h2>{data.postal_code}</h2>
            <div className="report-card__count">
              {data.representatives.length} representative{data.representatives.length === 1 ? "" : "s"}
              {total > 0 && <> · {total} site{total === 1 ? "" : "s"} tracked</>}
            </div>
          </div>
          {total > 0 && (
            <div className="postal-drawer__pct">
              <div className="postal-drawer__pct-num">{pctOutside}%</div>
              <div className="postal-drawer__pct-label">outside CA</div>
            </div>
          )}
        </header>

        <ul className="postal-drawer__list">
          {data.representatives.map((r, i) => <DrawerRepRow key={i} rep={r} />)}
        </ul>

        <footer className="report-card__foot">
          <div className="report-card__methodology">
            Map is filtered to your {data.representatives.length} elected officials.
            Press <strong>Clear</strong> on the search bar (or close this drawer)
            to restore the full national view.
          </div>
        </footer>
      </div>
    </aside>
  );
}

function DrawerRepRow({ rep }: { rep: PostalRep }) {
  const summary = rep.scan_summary;
  const total = summary?.websites ?? 0;
  const ca = summary?.canadian ?? 0;
  const pctOutside = total > 0 ? Math.round(100 * (total - ca) / total) : null;
  return (
    <li className="postal-drawer__row">
      <div className="postal-drawer__row-head">
        {rep.photo_url && <img src={rep.photo_url} alt="" className="postal-drawer__photo" />}
        <div className="postal-drawer__row-body">
          <div className="postal-drawer__row-name">
            {rep.politician_id ? (
              <Link to={`/politicians/${rep.politician_id}`} className="postal-drawer__row-name-link">
                {rep.name}
              </Link>
            ) : (
              rep.name
            )}
          </div>
          <div className="postal-drawer__row-office">
            {rep.elected_office}
            {rep.party && <> · {rep.party}</>}
          </div>
          <div className="postal-drawer__row-district">{rep.district}</div>
        </div>
        {pctOutside !== null && total > 0 && (
          <div className="postal-drawer__row-pct" title={`${ca}/${total} sites in Canada`}>
            <span>{pctOutside}%</span>
          </div>
        )}
      </div>

      {rep.sites.length > 0 ? (
        <ul className="postal-drawer__sites">
          {rep.sites.map((s, j) => {
            const tier = (s.tier ?? 6) as SovereigntyTier;
            const meta = TIER_META[tier];
            return (
              <li key={j}>
                <a href={s.url} target="_blank" rel="noopener">{s.hostname}</a>
                <span style={{ color: meta.color }}> · {meta.emoji} {meta.label}</span>
                {s.provider && <span className="postal-drawer__city"> · {s.provider}</span>}
                {s.city && <span className="postal-drawer__city"> · {s.city}{s.country ? `, ${s.country}` : ""}</span>}
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="postal-drawer__nowebsite">No personal/campaign website tracked</div>
      )}

      {rep.politician_id && (
        <div className="postal-drawer__row-actions">
          <Link to={`/politicians/${rep.politician_id}`} className="postal-drawer__row-profile-link">
            View profile →
          </Link>
        </div>
      )}
    </li>
  );
}
