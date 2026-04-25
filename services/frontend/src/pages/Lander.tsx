import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useCoverage, type CoverageJurisdiction } from "../hooks/useCoverage";

const POSTAL_RE = /^[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d$/;

const nf = new Intl.NumberFormat("en-CA");

interface CorpusStats {
  speeches: number;
  bills: number;
  politicians: number;
  liveJurisdictions: number;
  lastUpdate: string | null;
}

function aggregateCoverage(rows: CoverageJurisdiction[]): CorpusStats {
  let speeches = 0;
  let bills = 0;
  let politicians = 0;
  let liveJurisdictions = 0;
  let maxVerified: number | null = null;
  for (const r of rows) {
    speeches += r.speeches_count || 0;
    bills += r.bills_count || 0;
    politicians += r.politicians_count || 0;
    if (r.hansard_status === "live" || r.bills_status === "live") {
      liveJurisdictions++;
    }
    if (r.last_verified_at) {
      const t = new Date(r.last_verified_at).getTime();
      if (!Number.isNaN(t) && (maxVerified === null || t > maxVerified)) {
        maxVerified = t;
      }
    }
  }
  return {
    speeches,
    bills,
    politicians,
    liveJurisdictions,
    lastUpdate: maxVerified ? new Date(maxVerified).toISOString() : null,
  };
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "recently";
  const sec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} minute${min === 1 ? "" : "s"} ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} day${day === 1 ? "" : "s"} ago`;
  const wk = Math.floor(day / 7);
  if (wk < 5) return `${wk} week${wk === 1 ? "" : "s"} ago`;
  const mo = Math.floor(day / 30);
  return `${mo} month${mo === 1 ? "" : "s"} ago`;
}

export default function Lander() {
  useDocumentTitle(null);
  const navigate = useNavigate();
  const [postal, setPostal] = useState("");
  const [postalError, setPostalError] = useState<string | null>(null);
  const [hansard, setHansard] = useState("");

  const coverage = useCoverage();
  const stats = coverage.data ? aggregateCoverage(coverage.data.jurisdictions) : null;

  function submitPostal(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = postal.trim();
    if (!POSTAL_RE.test(trimmed)) {
      setPostalError("Enter a valid Canadian postal code (e.g. K1A 0A6)");
      return;
    }
    const canonical = trimmed.replace(/\s|-/g, "").toUpperCase();
    navigate(`/map?postal=${canonical}`);
  }

  function submitHansard(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = hansard.trim();
    if (!trimmed) return;
    navigate(`/search?q=${encodeURIComponent(trimmed)}`);
  }

  const searchHint = stats
    ? `Semantic search across ${nf.format(stats.speeches)} speeches.`
    : "Search what every politician has said on the record.";

  return (
    <div className="lander">
      <div className="lander__inner">
        <span className="lander__logo" aria-hidden="true">🍁</span>
        <h1 className="lander__title">Canadian Political Data</h1>

        {stats && stats.speeches > 0 && (
          <div className="lander__stats" aria-label="Corpus statistics">
            <div className="lander__stats-row">
              <span><span className="lander__stats-num">{nf.format(stats.speeches)}</span> speeches</span>
              <span className="lander__stats-sep" aria-hidden="true">·</span>
              <span><span className="lander__stats-num">{nf.format(stats.bills)}</span> bills</span>
              <span className="lander__stats-sep" aria-hidden="true">·</span>
              <span><span className="lander__stats-num">{nf.format(stats.politicians)}</span> politicians</span>
              <span className="lander__stats-sep" aria-hidden="true">·</span>
              <span><span className="lander__stats-num">{stats.liveJurisdictions}</span> jurisdictions live</span>
            </div>
            {stats.lastUpdate && (
              <div className="lander__stats-fresh">Updated {relativeTime(stats.lastUpdate)}</div>
            )}
          </div>
        )}

        <p className="lander__tagline">
          Search every speech, every bill, and every vote across Canada's federal and provincial legislatures.
        </p>
        <p className="lander__stance">
          Open data, public record &mdash; built to make democracy legible.
        </p>

        <div className="lander__forms">
          <form className="lander__form-card lander__form-card--hero" onSubmit={submitHansard}>
            <h2 className="lander__form-heading">
              <span aria-hidden="true">🔎</span> Search the record
            </h2>
            <div className="lander__find-row">
              <input
                id="lander-hansard"
                type="search"
                placeholder='Try "carbon pricing" or "housing crisis"'
                value={hansard}
                onChange={e => setHansard(e.target.value)}
                aria-label="Search Canadian parliamentary speeches"
              />
              <button type="submit" className="lander__btn lander__btn--primary">
                Search →
              </button>
            </div>
            <p className="lander__find-hint">{searchHint}</p>
          </form>

          <form className="lander__form-card" onSubmit={submitPostal}>
            <h2 className="lander__form-heading">
              <span aria-hidden="true">📍</span> Find your reps
            </h2>
            <div className="lander__find-row">
              <input
                id="lander-postal"
                type="text"
                placeholder="Postal code (K1A 0A6)"
                value={postal}
                onChange={e => { setPostal(e.target.value); setPostalError(null); }}
                aria-label="Canadian postal code"
                aria-invalid={postalError ? true : undefined}
                aria-describedby={postalError ? "lander-postal-error" : undefined}
                maxLength={7}
              />
              <button type="submit" className="lander__btn">
                Find →
              </button>
            </div>
            {postalError && (
              <div id="lander-postal-error" className="lander__find-error" role="alert">
                {postalError}
              </div>
            )}
            <p className="lander__find-hint">
              Look up your MP, MLA, and municipal councillors.
            </p>
          </form>
        </div>
      </div>
    </div>
  );
}
