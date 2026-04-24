import { useState } from "react";
import { fetchJson } from "../api";
import { TIER_META, type SovereigntyTier } from "../types";

interface ScanSummary {
  websites: number;
  canadian: number;
  cdn: number;
  us: number;
  foreign: number;
  worst_tier: number | null;
  best_tier: number | null;
}

interface SiteRow {
  url: string;
  hostname: string;
  label: string | null;
  tier: number | null;
  provider: string | null;
  country: string | null;
  city: string | null;
}

interface Rep {
  name: string;
  district: string;
  elected_office: string;
  party: string | null;
  email: string | null;
  photo_url: string | null;
  in_database: boolean;
  scan_summary: ScanSummary | null;
  sites: SiteRow[];
}

interface LookupResp {
  postal_code: string;
  representatives: Rep[];
}

const POSTAL_RE = /^[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d$/;

export function PostalLookup() {
  const [code, setCode] = useState("");
  const [data, setData] = useState<LookupResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = code.trim();
    if (!POSTAL_RE.test(trimmed)) {
      setError("Enter a valid Canadian postal code (e.g. K1A 0A6)");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const res = await fetchJson<LookupResp>(
        `/lookup/postcode/${trimmed.replace(/\s|-/g, "").toUpperCase()}`
      );
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lookup failed");
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="postal">
      <header>
        <h2>Find your representatives</h2>
        <p className="postal__sub">Where do <em>your</em> elected officials store their data?</p>
      </header>
      <form onSubmit={submit} className="postal__form">
        <input
          type="text"
          placeholder="K1A 0A6"
          value={code}
          onChange={e => setCode(e.target.value)}
          aria-label="Canadian postal code"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "postal-error" : undefined}
          maxLength={7}
        />
        <button type="submit" disabled={loading || code.trim().length < 6}>
          {loading ? "Looking up…" : "Look up"}
        </button>
      </form>
      {error && <p id="postal-error" className="postal__error" role="alert">{error}</p>}

      {data && (
        <div className="postal__results">
          <h3>{data.postal_code} — {data.representatives.length} representative{data.representatives.length === 1 ? "" : "s"}</h3>
          <ul className="postal__list">
            {data.representatives.map((r, i) => <RepCard key={i} rep={r} />)}
          </ul>
        </div>
      )}
    </section>
  );
}

function RepCard({ rep }: { rep: Rep }) {
  const summary = rep.scan_summary;
  return (
    <li className="rep-card">
      {rep.photo_url && <img className="rep-card__photo" src={rep.photo_url} alt="" />}
      <div className="rep-card__body">
        <div className="rep-card__head">
          <div>
            <div className="rep-card__name">{rep.name}</div>
            <div className="rep-card__office">
              {rep.elected_office} · {rep.district}{rep.party ? ` · ${rep.party}` : ""}
            </div>
          </div>
          {summary && summary.websites > 0 && (
            <SovereigntySummary summary={summary} />
          )}
        </div>

        {!rep.in_database && (
          <div className="rep-card__nodata">Not yet in our scan database — we may not track this level of office.</div>
        )}
        {rep.in_database && summary && summary.websites === 0 && (
          <div className="rep-card__nodata">No personal/campaign website found for this representative.</div>
        )}

        {rep.sites.length > 0 && (
          <ul className="rep-card__sites">
            {rep.sites.map((s, i) => {
              const tier = (s.tier ?? 6) as SovereigntyTier;
              const meta = TIER_META[tier];
              return (
                <li key={i} className="rep-card__site">
                  <a href={s.url} target="_blank" rel="noopener">{s.hostname}</a>
                  <span className="rep-card__tier" style={{ color: meta.color }}>
                    {meta.emoji} {meta.label}
                  </span>
                  <span className="rep-card__host">
                    {s.provider ?? "—"}{s.city ? ` · ${s.city}` : ""}{s.country ? `, ${s.country}` : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </li>
  );
}

function SovereigntySummary({ summary }: { summary: ScanSummary }) {
  const total = summary.websites;
  if (total === 0) return null;
  const ca = summary.canadian;
  const notCa = total - ca;
  const pct = Math.round(100 * notCa / total);
  return (
    <div className="rep-card__sov" title={`${ca} Canadian, ${summary.cdn} CDN, ${summary.us} US, ${summary.foreign} other`}>
      <div className="rep-card__sov-num">{pct}%</div>
      <div className="rep-card__sov-label">outside CA</div>
    </div>
  );
}
