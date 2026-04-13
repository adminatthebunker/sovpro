import { useEffect, useMemo } from "react";
import { MapContainer, TileLayer, GeoJSON, LayerGroup, useMap } from "react-leaflet";
import L from "leaflet";
import { useFetch } from "../hooks/useFetch";
import type { GeoCollection, ReferendumSideSummary, SovereigntyTier } from "../types";
import { TIER_META } from "../types";
import { AntLines } from "./AntLines";
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
  const { data: mapData } = useFetch<GeoCollection>("/map/referendum");
  const { data: partyStats } = useFetch<AlbertaStatsResponse>("/parties");

  if (loading) return <div className="ref">Loading referendum data…</div>;
  if (error)   return <div className="ref ref--error">{error.message}</div>;
  if (!data)   return null;

  const irony = data.irony_score ||
    "Organizations advocating Alberta leave Canada for sovereignty store their website data outside Canada.";

  const totalSites = (data.leave_side.total_websites || 0) + (data.stay_side.total_websites || 0);
  const totalCA = (data.leave_side.hosted_in_canada || 0) + (data.stay_side.hosted_in_canada || 0);
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
        <h2 className="ref__hero-headline">
          <span className="ref__hero-headline-num">{pctOutside}%</span>
          {" "}of referendum-organization websites are hosted{" "}
          <strong>outside Canada</strong>.
        </h2>
        <p className="ref__hero-sub">{irony}</p>
        <div className="ref__hero-strip">
          <div className="ref__hero-strip-card ref__hero-strip-card--leave">
            <div className="ref__hero-strip-num">{leaveOrgs}</div>
            <div className="ref__hero-strip-label">organizations advocating Alberta leave Canada</div>
            <div className="ref__hero-strip-sub">{data.leave_side.hosted_in_canada}/{data.leave_side.total_websites} sites in Canada</div>
          </div>
          <div className="ref__hero-strip-card ref__hero-strip-card--stay">
            <div className="ref__hero-strip-num">{stayOrgs}</div>
            <div className="ref__hero-strip-label">organizations campaigning to stay in Canada</div>
            <div className="ref__hero-strip-sub">{data.stay_side.hosted_in_canada}/{data.stay_side.total_websites} sites in Canada</div>
          </div>
        </div>
      </header>

      {mapData && <RefMiniMap data={mapData} />}

      <div className="ref__grid">
        <SideCard side="leave" title="LEAVE Canada" subtitle="Alberta sovereignty advocates" data={data.leave_side} />
        <SideCard side="stay"  title="STAY in Canada" subtitle="Federalist organizations" data={data.stay_side} />
      </div>

      <AlbertaPoliticians
        partyStats={partyStats}
        reportParty={reportParty ?? null}
        onShowReport={onShowReport}
        onCloseReport={onCloseReport}
      />

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
              const ca = p.ca || 0;
              const pct = total > 0 ? Math.round(100 * ca / total) : 0;
              const grade =
                pct >= 85 ? "A" : pct >= 70 ? "B" : pct >= 50 ? "C" : pct >= 30 ? "D" : "F";
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
                    <span>{pct}% in Canada</span>
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

function SideCard({
  side, title, subtitle, data,
}: { side: SideKey; title: string; subtitle: string; data: ReferendumSideSummary }) {
  const total = data.total_websites || 0;
  const ca = data.hosted_in_canada || 0;
  const allCA = total > 0 && ca === total;
  const noneCA = ca === 0;
  const bigClass =
    "side-card__big " +
    (noneCA ? "side-card__big--bad" : allCA ? "side-card__big--good" : "side-card__big--warn");

  return (
    <div className={`side-card side-card--${side}`}>
      <header className="side-card__header">
        <div className="side-card__badge">{side === "leave" ? "LEAVE" : "STAY"}</div>
        <h3>{title}</h3>
        <div className="side-card__subtitle">{subtitle}</div>
      </header>

      <div className="side-card__stat">
        <span className={bigClass}>{ca}</span>
        <span className="side-card__denom">/ {total}</span>
        <div className="side-card__stat-label">websites hosted in Canada</div>
      </div>

      <div className="side-card__ticks">
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

/** Auto-fit the mini map to its features once. */
function FitOnce({ data }: { data: GeoCollection }) {
  const map = useMap();
  useEffect(() => {
    if (!data?.features?.length) return;
    const bounds = L.latLngBounds([]);
    let extended = false;
    for (const f of data.features) {
      try {
        const layer = L.geoJSON(f as GeoJSON.GeoJsonObject);
        const b = layer.getBounds();
        if (b.isValid()) { bounds.extend(b); extended = true; }
      } catch { /* noop */ }
    }
    if (extended) {
      setTimeout(() => map.fitBounds(bounds, { padding: [30, 30], animate: false }), 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);
  return null;
}

function RefMiniMap({ data }: { data: GeoCollection }) {
  const { polygons, servers, lines } = useMemo(() => {
    const polys: GeoCollection = { type: "FeatureCollection", features: [] };
    const srv:   GeoCollection = { type: "FeatureCollection", features: [] };
    const lns:   GeoCollection = { type: "FeatureCollection", features: [] };
    for (const f of data.features) {
      const kind = (f.properties as { kind?: string })?.kind;
      if (kind === "constituency" || kind === "province" || kind === "region") polys.features.push(f);
      else if (kind === "server") srv.features.push(f);
      else if (kind === "connection") lns.features.push(f);
      else if ((f.geometry as { type?: string })?.type === "Polygon" ||
               (f.geometry as { type?: string })?.type === "MultiPolygon") polys.features.push(f);
      else if ((f.geometry as { type?: string })?.type === "Point") srv.features.push(f);
      else if ((f.geometry as { type?: string })?.type === "LineString") lns.features.push(f);
    }
    return { polygons: polys, servers: srv, lines: lns };
  }, [data]);

  return (
    <div className="ref__map-wrap">
      <div className="ref__map-head">
        <h3>Where the servers actually live</h3>
        <p>Alberta outline with each campaign&apos;s server pin. Animated lines trace data flowing to foreign hosts.</p>
      </div>
      <div className="ref__map">
        <MapContainer
          center={[52, -100]}
          zoom={3}
          minZoom={2}
          scrollWheelZoom={false}
          style={{ height: "100%", width: "100%", background: "#0b1220" }}
          preferCanvas
          zoomAnimation={false}
          fadeAnimation={false}
          markerZoomAnimation={false}
        >
          <FitOnce data={data} />
          <TileLayer
            attribution='&copy; OpenStreetMap, &copy; CARTO'
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
          <GeoJSON
            key={`ref-poly-${polygons.features.length}`}
            data={polygons as GeoJSON.FeatureCollection}
            style={() => ({
              color: "#f59e0b",
              weight: 1.2,
              fillColor: "#f59e0b",
              fillOpacity: 0.08,
            })}
          />
          <GeoJSON
            key={`ref-lines-ca-${lines.features.length}`}
            data={{
              ...lines,
              features: lines.features.filter((f) => {
                const t = ((f.properties as { sovereignty_tier?: number })?.sovereignty_tier ?? 6) as SovereigntyTier;
                return t === 1 || t === 2;
              }),
            } as GeoJSON.FeatureCollection}
            style={(f) => {
              const tier = ((f?.properties as { sovereignty_tier?: number })?.sovereignty_tier ?? 6) as SovereigntyTier;
              return { color: TIER_META[tier]?.color ?? "#64748b", weight: 0.8, opacity: 0.55 };
            }}
          />
          <LayerGroup>
            <AntLines data={lines} />
          </LayerGroup>
          <GeoJSON
            key={`ref-srv-${servers.features.length}`}
            data={servers as GeoJSON.FeatureCollection}
            pointToLayer={(feature, latlng) => {
              const p = feature.properties || {};
              const tier = (p.sovereignty_tier ?? 6) as SovereigntyTier;
              const meta = TIER_META[tier];
              const side = String(p.side ?? "").toLowerCase();
              const ring = side === "leave" ? "#ef4444" : side === "stay" ? "#3b82f6" : "#0b1220";
              return L.circleMarker(latlng, {
                radius: 7,
                color: ring,
                weight: 2,
                fillColor: meta.color,
                fillOpacity: 0.95,
              });
            }}
            onEachFeature={(f, layer) => {
              const p = f.properties || {};
              const title = String(p.organization_name ?? p.org_name ?? p.hostname ?? "");
              const tier = (p.sovereignty_tier ?? 6) as SovereigntyTier;
              const meta = TIER_META[tier];
              layer.bindTooltip(
                `<strong>${escapeHtml(title)}</strong><br/>` +
                `${meta.emoji} ${escapeHtml(meta.label)}<br/>` +
                `${escapeHtml(String(p.hosting_provider ?? "unknown"))} · ` +
                `${escapeHtml(String(p.city ?? p.ip_city ?? ""))} ${escapeHtml(String(p.hosting_country ?? p.ip_country ?? ""))}`
              );
            }}
          />
        </MapContainer>
      </div>
      <div className="ref__map-legend">
        <span className="ref__dot ref__dot--leave" /> Leave side server
        <span className="ref__dot ref__dot--stay" /> Stay side server
        <span className="ref__dot ref__dot--ab" /> Alberta
      </div>
    </div>
  );
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
