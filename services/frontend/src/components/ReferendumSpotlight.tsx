import { useFetch } from "../hooks/useFetch";
import type { ReferendumSideSummary, SovereigntyTier } from "../types";
import { TIER_META } from "../types";
import { MapView } from "./MapView";
import { partyColor } from "./PartyFilter";
import { PartyReportCard } from "./PartyReportCard";

interface RefResponse {
  leave_side: ReferendumSideSummary;
  stay_side: ReferendumSideSummary;
  irony_score: string;
}

type SideKey = "leave" | "stay";

interface AlbertaStatsResponse {
  parties: Array<{
    party: string;
    politicians: number;
    sites: number;
    personal: number;
    party_managed: number;
    ca: number;
    ab: number;
    us: number;
    cdn: number;
    foreign: number;
  }>;
}

interface Props {
  reportParty?: string | null;
  onShowReport?: (party: string) => void;
  onCloseReport?: () => void;
}

const AB_PARTIES = [
  "United Conservative Party",
  "Alberta New Democratic Party",
];

export function ReferendumSpotlight({ reportParty, onShowReport, onCloseReport }: Props) {
  const { data, loading, error } = useFetch<RefResponse>("/stats/referendum");
  const { data: partyStats } = useFetch<AlbertaStatsResponse>("/parties");

  if (loading) return <div className="ref">Loading referendum data…</div>;
  if (error)   return <div className="ref ref--error">{error.message}</div>;
  if (!data)   return null;

  const irony = data.irony_score ||
    "Organizations advocating Alberta leave Canada for sovereignty store their website data outside Canada.";

  const totalSites = (data.leave_side.total_websites || 0) + (data.stay_side.total_websites || 0);
  const totalCA = (data.leave_side.hosted_in_canada || 0) + (data.stay_side.hosted_in_canada || 0);
  const totalAB = (data.leave_side.hosted_in_alberta || 0) + (data.stay_side.hosted_in_alberta || 0);
  const totalOutside = totalSites - totalCA;
  const pctOutside = totalSites > 0 ? Math.round(100 * totalOutside / totalSites) : 0;
  const daysUntil = Math.max(0, Math.ceil(
    (new Date("2026-10-19T00:00:00-06:00").getTime() - Date.now()) / 86400000
  ));
  const leaveOrgs = data.leave_side.orgs?.length ?? 0;
  const stayOrgs = data.stay_side.orgs?.length ?? 0;

  return (
    <section className="ref">
      <header className="ref__hero">
        <div className="ref__hero-top">
          <div className="ref__hero-eyebrow">Alberta Sovereignty Referendum · October 19, 2026</div>
          <div className="ref__hero-countdown">
            <span className="ref__hero-countdown-num">{daysUntil}</span>
            <span className="ref__hero-countdown-label">days to vote</span>
          </div>
        </div>
        <h2 className="ref__hero-tagline">
          For Canada, against Canada, <em>mostly American</em>.
        </h2>

        <div className="ref__hero-killer">
          <span className="ref__hero-killer-num">{totalAB}</span>
          <span className="ref__hero-killer-of">of</span>
          <span className="ref__hero-killer-denom">{totalSites}</span>
          <span className="ref__hero-killer-label">
            referendum-organization websites are physically hosted in Alberta.
          </span>
        </div>

        <p className="ref__hero-sub">
          {pctOutside}% are hosted outside Canada entirely. {irony}
        </p>

        <div className="ref__hero-strip">
          <SideStrip
            side="leave"
            orgs={leaveOrgs}
            label="advocating Alberta leave Canada"
            ab={data.leave_side.hosted_in_alberta}
            total={data.leave_side.total_websites}
          />
          <SideStrip
            side="stay"
            orgs={stayOrgs}
            label="campaigning to stay in Canada"
            ab={data.stay_side.hosted_in_alberta}
            total={data.stay_side.total_websites}
          />
        </div>
      </header>

      <AlbertaPoliticians
        partyStats={partyStats}
        reportParty={reportParty ?? null}
        onShowReport={onShowReport}
        onCloseReport={onCloseReport}
      />

      <div className="ref__grid">
        <SideCard side="leave" title="LEAVE Canada" subtitle="Alberta sovereignty advocates" data={data.leave_side} />
        <SideCard side="stay"  title="STAY in Canada" subtitle="Federalist organizations" data={data.stay_side} />
      </div>

      <p className="ref__footnote">
        Both sides of Alberta&apos;s sovereignty debate host their digital infrastructure outside Canada.
        If you can&apos;t keep your website in Canada, what exactly are you liberating?
      </p>
    </section>
  );
}

/** Section showing all Alberta MLAs + councillors on the map plus quick
 *  report-card shortcuts for UCP and Alberta NDP. */
function AlbertaPoliticians({
  partyStats,
  reportParty,
  onShowReport,
  onCloseReport,
}: {
  partyStats: AlbertaStatsResponse | null;
  reportParty: string | null;
  onShowReport?: (party: string) => void;
  onCloseReport?: () => void;
}) {
  const abPartyRows =
    partyStats?.parties.filter(p => AB_PARTIES.includes(p.party)) ?? [];

  return (
    <section className="ref__ab">
      <header className="ref__ab-head">
        <h3>Every Alberta politician we track</h3>
        <p>
          MLAs, Edmonton + Calgary councillors, and the referendum organizations &mdash;
          all on one map. Click any riding for the politician&apos;s photo, party,
          and where their data lives.
        </p>
      </header>

      <div className={`map-with-drawer ${reportParty ? "is-open" : ""}`}>
        <div className="map-with-drawer__map ref__ab-map">
          <MapView
            filters={{
              layer: "all",
              level: undefined,
              province: "AB",
              party: undefined,
              includeNoData: true,
              politicianIds: undefined,
            }}
          />
        </div>
        {reportParty && (
          <PartyReportCard
            party={reportParty}
            partyColor={partyColor(reportParty)}
            onClose={() => onCloseReport?.()}
          />
        )}
      </div>

      {abPartyRows.length > 0 && (
        <div className="ref__ab-parties">
          <h4>Provincial parties &mdash; sovereignty report</h4>
          <div className="ref__ab-party-grid">
            {abPartyRows.map(p => {
              const total = p.sites || 0;
              const ab = p.ab || 0;
              const ca = p.ca || 0;
              const pctAB = total > 0 ? Math.round(100 * ab / total) : 0;
              // Grade weights Alberta-hosted highest; CA-but-not-AB is partial credit.
              const score = total > 0 ? (ab + 0.5 * (ca - ab)) / total : 0;
              const pctScore = Math.round(100 * score);
              const grade =
                pctScore >= 70 ? "A" : pctScore >= 55 ? "B" : pctScore >= 35 ? "C" : pctScore >= 15 ? "D" : "F";
              const gradeClass = grade.toLowerCase();
              return (
                <button
                  key={p.party}
                  className={`ref__ab-party report-card__grade--${gradeClass}`}
                  style={{ borderColor: partyColor(p.party) }}
                  onClick={() => onShowReport?.(p.party)}
                  title={`Open ${p.party} report card`}
                >
                  <div className="ref__ab-party-name" style={{ color: partyColor(p.party) }}>
                    {p.party}
                  </div>
                  <div className="ref__ab-party-grade">{grade}</div>
                  <div className="ref__ab-party-stats">
                    <span>{p.politicians} MLAs</span>
                    <span>·</span>
                    <span>{pctAB}% in Alberta</span>
                  </div>
                  <div className="ref__ab-party-cta">View full report card →</div>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

/** Compact "N orgs · M/N sites in AB" strip used in the hero. */
function SideStrip({
  side, orgs, label, ab, total,
}: { side: SideKey; orgs: number; label: string; ab: number; total: number }) {
  return (
    <div className={`ref__hero-strip-card ref__hero-strip-card--${side}`}>
      <div className="ref__hero-strip-line">
        <span className="ref__hero-strip-num">{orgs}</span>
        <span className="ref__hero-strip-label">{label}</span>
      </div>
      <div className="ref__hero-strip-sub">
        <strong>{ab}/{total}</strong> sites hosted in Alberta
      </div>
    </div>
  );
}

function SideCard({
  side, title, subtitle, data,
}: { side: SideKey; title: string; subtitle: string; data: ReferendumSideSummary }) {
  const total = data.total_websites || 0;
  const ab = data.hosted_in_alberta || 0;
  const allAB = total > 0 && ab === total;
  const noneAB = ab === 0;
  const bigClass =
    "side-card__big " +
    (noneAB ? "side-card__big--bad" : allAB ? "side-card__big--good" : "side-card__big--warn");

  return (
    <div className={`side-card side-card--${side}`}>
      <header className="side-card__header">
        <div className="side-card__badge">{side === "leave" ? "LEAVE" : "STAY"}</div>
        <h3>{title}</h3>
        <div className="side-card__subtitle">{subtitle}</div>
      </header>

      <div className="side-card__stat">
        <span className={bigClass}>{ab}</span>
        <span className="side-card__denom">/ {total}</span>
        <div className="side-card__stat-label">websites hosted in Alberta</div>
      </div>

      <div className="side-card__ticks">
        <div className="side-card__tick"><span className="side-card__tick-n">{data.hosted_in_canada}</span> elsewhere in Canada</div>
        <div className="side-card__tick"><span className="side-card__tick-n">{data.hosted_in_us}</span> in US</div>
        <div className="side-card__tick"><span className="side-card__tick-n">{data.cdn_fronted}</span> CDN-fronted</div>
      </div>

      <ul className="side-card__orgs">
        {(data.websites ?? []).map((w, i) => {
          const tier = (w.sovereignty_tier ?? 6) as SovereigntyTier;
          const meta = TIER_META[tier];
          const loc = [w.ip_city, w.ip_country].filter(Boolean).join(", ");
          return (
            <li key={i} className="side-card__row">
              <div className="side-card__row-head">
                <span className="side-card__tier" style={{ color: meta.color }} title={meta.label}>
                  {meta.emoji}
                </span>
                <span className="side-card__org">{w.org_name}</span>
              </div>
              <div className="side-card__row-meta">
                <span className="side-card__tier-label" style={{ color: meta.color }}>{meta.label}</span>
                {w.hosting_provider ? <span> · {w.hosting_provider}</span> : null}
                {loc ? <span> · {loc}</span> : null}
              </div>
              <div className="side-card__row-url">
                <a href={w.website_url} target="_blank" rel="noopener noreferrer">{w.hostname}</a>
              </div>
            </li>
          );
        })}
      </ul>

      {data.providers.length > 0 && (
        <div className="side-card__providers">
          <span className="side-card__providers-label">Providers used</span>
          <div className="side-card__providers-chips">
            {data.providers.map((p) => (
              <span key={p} className="side-card__chip">{p}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Old RefMiniMap + escapeHtml helpers removed. The AB MapView inside
// AlbertaPoliticians now renders every AB riding + every referendum
// org server pin in one combined view via /map/geojson?province=AB&group=all.
