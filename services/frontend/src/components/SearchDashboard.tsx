import { Link } from "react-router-dom";
import { useSpeechFacets, type FacetsResponse } from "../hooks/useSpeechFacets";
import type { SpeechSearchFilter } from "../hooks/useSpeechSearch";
import { partyColor } from "./PartyFilter";
import { PieChart, type PieSegment } from "./charts/PieChart";
import { BarList } from "./charts/BarList";
import { YearHistogram } from "./charts/YearHistogram";
import "../styles/search-dashboard.css";

// Speech_chunks.party_at_time uses upstream codes ("Lib", "CPC", "NDP",
// "BQ", "GP", "NPD" (FR-NDP), …). partyColor() in PartyFilter keys on
// long names. Normalize before we color + label.
const PARTY_LABEL: Record<string, string> = {
  Lib: "Liberal",
  CPC: "Conservative",
  NDP: "NDP",
  NPD: "NDP",
  BQ: "Bloc Québécois",
  GP: "Green Party",
  PV: "Green Party",
  Ref: "Reform",
  PCC: "Progressive Conservative",
  PC: "Progressive Conservative",
  "PC/DR": "PC/DR coalition",
  Ind: "Independent",
  "Canadian Alliance": "Canadian Alliance",
  CCF: "CCF",
  GPQ: "Green Party of Quebec",
};

function normalizeParty(code: string | null): { label: string; color: string } {
  if (!code) return { label: "Unresolved / Chair", color: "#64748b" };
  const label = PARTY_LABEL[code] ?? code;
  // partyColor() falls back to a neutral gray for unknowns — good default.
  const color = partyColor(label);
  return { label, color };
}

interface SearchDashboardProps {
  filter: SpeechSearchFilter;
  /** When false, the dashboard renders a collapsed empty state and makes no
   *  network request. Mirrors the search page's `enabled` flag. */
  enabled: boolean;
  /** Total match count from the /speeches endpoint, used to contextualize
   *  "Analyzed top 200 of N matches". Pass -1 to indicate unknown. */
  totalMatches?: number;
}

export function SearchDashboard({ filter, enabled, totalMatches }: SearchDashboardProps) {
  const { data, loading, error } = useSpeechFacets(filter, enabled);

  if (!enabled) return null;

  if (loading && !data) {
    return (
      <section className="dashboard dashboard--loading" aria-label="Result dashboard">
        <div className="dashboard__hint">Analyzing result shape…</div>
      </section>
    );
  }
  if (error) {
    return (
      <section className="dashboard dashboard--error" aria-label="Result dashboard">
        <div className="dashboard__hint">Couldn't load dashboard: {error.message}</div>
      </section>
    );
  }
  if (!data || data.analyzed_count === 0) return null;

  return (
    <details className="dashboard">
      <summary className="dashboard__summary">
        <DashboardHeadline data={data} totalMatches={totalMatches} />
      </summary>
      <div className="dashboard__grid">
        <PartyTile data={data} />
        <SpeakerTile data={data} />
        <YearTile data={data} />
        {data.keyword_overlap && <OverlapTile data={data} />}
      </div>
    </details>
  );
}

function DashboardHeadline({ data, totalMatches }: { data: FacetsResponse; totalMatches?: number }) {
  const lang = data.by_language;
  const langStrip =
    lang.length > 0
      ? lang.map((l) => `${l.language.toUpperCase()}: ${l.count}`).join(" · ")
      : null;
  const analyzedText =
    typeof totalMatches === "number" && totalMatches > data.analyzed_count
      ? `Analyzed top ${data.analyzed_count.toLocaleString()} of ${totalMatches.toLocaleString()} matches`
      : `Analyzed ${data.analyzed_count.toLocaleString()} match${data.analyzed_count === 1 ? "" : "es"}`;
  return (
    <div className="dashboard__headline">
      <span className="dashboard__headline-main">{analyzedText}</span>
      {langStrip && <span className="dashboard__headline-lang"> · {langStrip}</span>}
      <span className="dashboard__headline-toggle" aria-hidden="true">▾</span>
    </div>
  );
}

function PartyTile({ data }: { data: FacetsResponse }) {
  const rows = data.by_party.slice(0, 7);
  const segments: PieSegment[] = rows.map((r) => {
    const { label, color } = normalizeParty(r.party);
    const sim = r.avg_similarity !== null ? ` · ${(r.avg_similarity * 100).toFixed(0)}% avg similarity` : "";
    return {
      label,
      value: r.count,
      color,
      tooltip: `${label}: ${r.count}${sim}`,
    };
  });
  return (
    <div className="dashboard__tile">
      <h4 className="dashboard__tile-title">By party</h4>
      <div className="dashboard__pie-row">
        <PieChart segments={segments} size={140} strokeWidth={24} />
        <ul className="dashboard__legend">
          {rows.map((r) => {
            const { label, color } = normalizeParty(r.party);
            return (
              <li key={r.party ?? "_null"} className="dashboard__legend-row">
                <span className="dashboard__swatch" style={{ background: color }} />
                <span className="dashboard__legend-label">{label}</span>
                <span className="dashboard__legend-value">{r.count}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function SpeakerTile({ data }: { data: FacetsResponse }) {
  const rows = data.by_politician.map((r) => ({
    key: r.politician?.id ?? "_unresolved",
    label: r.politician ? (
      <Link to={`/politicians/${r.politician.id}`} className="dashboard__speaker-link">
        {r.politician.name}
      </Link>
    ) : (
      <span className="dashboard__speaker-unresolved">Chair / Speaker</span>
    ),
    value: r.count,
    meta: r.avg_similarity !== null ? `${(r.avg_similarity * 100).toFixed(0)}%` : undefined,
  }));
  return (
    <div className="dashboard__tile">
      <h4 className="dashboard__tile-title">Top speakers</h4>
      <BarList rows={rows} />
    </div>
  );
}

function YearTile({ data }: { data: FacetsResponse }) {
  return (
    <div className="dashboard__tile">
      <h4 className="dashboard__tile-title">When it was said</h4>
      <YearHistogram bins={data.by_year} height={120} />
    </div>
  );
}

function OverlapTile({ data }: { data: FacetsResponse }) {
  const ov = data.keyword_overlap!;
  const total = ov.both + ov.semantic_only;
  const semanticPct = total > 0 ? (ov.semantic_only / total) * 100 : 0;
  const segments: PieSegment[] = [
    {
      label: "Also matches keywords",
      value: ov.both,
      color: "#0891b2",
      tooltip: `Also matches keywords: ${ov.both} (${((ov.both / total) * 100).toFixed(0)}%)`,
    },
    {
      label: "Semantic only",
      value: ov.semantic_only,
      color: "#e11d48",
      tooltip: `Semantic only: ${ov.semantic_only} (${((ov.semantic_only / total) * 100).toFixed(0)}%)`,
    },
  ];
  return (
    <div className="dashboard__tile">
      <h4 className="dashboard__tile-title">Keyword overlap</h4>
      <div className="dashboard__pie-row">
        <PieChart segments={segments} size={140} strokeWidth={24} />
        <ul className="dashboard__legend">
          <li className="dashboard__legend-row">
            <span className="dashboard__swatch" style={{ background: "#0891b2" }} />
            <span className="dashboard__legend-label">Also keyword-match</span>
            <span className="dashboard__legend-value">{ov.both}</span>
          </li>
          <li className="dashboard__legend-row">
            <span className="dashboard__swatch" style={{ background: "#e11d48" }} />
            <span className="dashboard__legend-label">Semantic only</span>
            <span className="dashboard__legend-value">{ov.semantic_only}</span>
          </li>
        </ul>
      </div>
      <p className="dashboard__tile-foot">
        {semanticPct >= 20
          ? `${semanticPct.toFixed(0)}% of results were found by meaning, not exact words — the vector model is doing real work here.`
          : `Most of these results also match the exact keywords — for this phrase, keyword search would get you close.`}
      </p>
    </div>
  );
}
