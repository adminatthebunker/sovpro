import { useState } from "react";
import { fetchJson } from "../api";
import { TIER_META, type SovereigntyTier } from "../types";

interface ScanSummary {
  websites: number;
  canadian: number;
  cdn: number;
  us: number;
  foreign: number;
}

interface SiteRow {
  url: string; hostname: string;
  tier: number | null; provider: string | null; country: string | null; city: string | null;
}

interface Rep {
  politician_id: string | null;
  name: string;
  district: string;
  elected_office: string;
  party: string | null;
  photo_url: string | null;
  in_database: boolean;
  scan_summary: ScanSummary | null;
  sites: SiteRow[];
}

interface LookupResp { postal_code: string; representatives: Rep[]; }

const POSTAL_RE = /^[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d$/;

interface Props {
  /** Called when a lookup completes — array of politician IDs to filter the map by, or null to clear */
  onResult: (politicianIds: string[] | null, postalCode: string | null) => void;
}

/**
 * Inline postal-code search bar that lives above the map.
 * On a successful lookup it pushes the matched politician IDs upward so
 * the map (and map-side state) can filter to just those reps.
 */
export function PostalLookupBar({ onResult }: Props) {
  const [code, setCode] = useState("");
  const [data, setData] = useState<LookupResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = code.trim();
    if (!POSTAL_RE.test(trimmed)) {
      setError("Enter a valid Canadian postal code (e.g. K1A 0A6)");
      return;
    }
    setError(null); setLoading(true);
    try {
      const res = await fetchJson<LookupResp>(
        `/lookup/postcode/${trimmed.replace(/\s|-/g, "").toUpperCase()}`
      );
      setData(res);
      const ids = res.representatives.map(r => r.politician_id).filter((x): x is string => !!x);
      onResult(ids.length ? ids : null, res.postal_code);
      setExpanded(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lookup failed");
      setData(null);
      onResult(null, null);
    } finally {
      setLoading(false);
    }
  }

  function clear() {
    setCode("");
    setData(null);
    setError(null);
    onResult(null, null);
  }

  return (
    <section className="postal-bar">
      <form onSubmit={submit} className="postal-bar__form">
        <label className="postal-bar__label">
          <span className="postal-bar__icon">📍</span>
          Find Your Data
        </label>
        <input
          type="text"
          placeholder="Postal code  (K1A 0A6)"
          value={code}
          onChange={e => setCode(e.target.value)}
          aria-label="Canadian postal code"
          maxLength={7}
        />
        <button type="submit" disabled={loading || code.trim().length < 6}>
          {loading ? "…" : "Look up"}
        </button>
        {data && (
          <button type="button" className="postal-bar__clear" onClick={clear}>
            Clear
          </button>
        )}
      </form>
      {error && <div className="postal-bar__error">{error}</div>}
      {data && (
        <div className="postal-bar__result">
          <div className="postal-bar__result-head">
            <strong>{data.postal_code}</strong>
            <span className="postal-bar__result-count">
              {data.representatives.length} representative{data.representatives.length === 1 ? "" : "s"}
            </span>
            <button
              type="button"
              className="postal-bar__toggle"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? "Hide details" : "Show details"}
            </button>
          </div>
          {expanded && (
            <ul className="postal-bar__list">
              {data.representatives.map((r, i) => <RepRow key={i} rep={r} />)}
            </ul>
          )}
          <p className="postal-bar__note">Map filtered to your representatives. Press <kbd>Clear</kbd> to see everyone again.</p>
        </div>
      )}
    </section>
  );
}

function RepRow({ rep }: { rep: Rep }) {
  const summary = rep.scan_summary;
  const total = summary?.websites ?? 0;
  const ca = summary?.canadian ?? 0;
  const pctOutside = total > 0 ? Math.round(100 * (total - ca) / total) : null;
  return (
    <li className="postal-bar__row">
      {rep.photo_url && <img src={rep.photo_url} alt="" className="postal-bar__photo" />}
      <div className="postal-bar__row-body">
        <div className="postal-bar__row-name">
          {rep.name}
          {rep.party && <span className="postal-bar__row-party"> · {rep.party}</span>}
        </div>
        <div className="postal-bar__row-office">{rep.elected_office} · {rep.district}</div>
        {rep.sites.length > 0 ? (
          <ul className="postal-bar__sites">
            {rep.sites.map((s, j) => {
              const tier = (s.tier ?? 6) as SovereigntyTier;
              const meta = TIER_META[tier];
              return (
                <li key={j}>
                  <a href={s.url} target="_blank" rel="noopener">{s.hostname}</a>
                  <span style={{ color: meta.color }}> · {meta.emoji} {meta.label}</span>
                  {s.city && <span className="postal-bar__city"> · {s.city}{s.country ? `, ${s.country}` : ""}</span>}
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="postal-bar__nowebsite">No personal/campaign website tracked</div>
        )}
      </div>
      {pctOutside !== null && total > 0 && (
        <div className="postal-bar__row-pct" title={`${ca}/${total} sites in Canada`}>
          <span className="postal-bar__row-pct-num">{pctOutside}%</span>
          <span className="postal-bar__row-pct-label">outside CA</span>
        </div>
      )}
    </li>
  );
}
