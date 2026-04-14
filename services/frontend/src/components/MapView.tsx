import { useEffect, useMemo } from "react";
import { MapContainer, TileLayer, GeoJSON, LayersControl, useMap } from "react-leaflet";
import L from "leaflet";
import type { GeoCollection, SovereigntyTier } from "../types";
import { TIER_META } from "../types";
import { useFetch } from "../hooks/useFetch";
import type { FilterState } from "./Filters";

/** Auto-fits the map to the bounding box of the given features when the
 *  feature set is small (i.e. user just narrowed via postal lookup). */
function FitToFeatures({ features }: { features: GeoJSON.Feature[] }) {
  const map = useMap();
  useEffect(() => {
    if (!features.length || features.length > 80) return;
    const bounds = L.latLngBounds([]);
    let extended = false;
    for (const f of features) {
      try {
        const layer = L.geoJSON(f as GeoJSON.GeoJsonObject);
        const b = layer.getBounds();
        if (b.isValid()) {
          bounds.extend(b);
          extended = true;
        }
      } catch { /* noop */ }
    }
    if (extended) {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 9, animate: false });
    }
  }, [features, map]);
  return null;
}

interface Props {
  filters: FilterState;
}

const CANADA_CENTER: [number, number] = [56.1304, -106.3468];

/** Singleton SVG renderer used ONLY for the animated flow lines. Everything
 *  else inherits the map-level Canvas renderer (preferCanvas). */
const svgFlowRenderer = L.svg({ padding: 0.5 });

// Fix default marker icons under bundlers
// @ts-expect-error - leaflet icon fallback
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  iconUrl:       "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  shadowUrl:     "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
});

export function MapView({ filters }: Props) {
  // Common query params shared by both fetches. Pins-first strategy: fetch
  // pins+lines (tiny) in parallel with polygons (large). Map becomes
  // interactive as soon as pins land; polygons stream in ~500ms later and
  // just re-render in place.
  const baseParams = useMemo(() => {
    const params = new URLSearchParams();
    if (filters.level) params.set("level", filters.level);
    if (filters.province) params.set("province", filters.province);
    if (filters.party) params.set("party", filters.party);
    if (filters.includeNoData) params.set("include_no_data", "true");
    if (filters.politicianIds && filters.politicianIds.length > 0) {
      params.set("politician_ids", filters.politicianIds.join(","));
    }
    params.set("group", filters.layer === "all" ? "all" : filters.layer);
    return params.toString();
  }, [filters]);

  const pinsPath = `/map/geojson?${baseParams}&kinds=server,connection`;
  const polysPath = `/map/geojson?${baseParams}&kinds=constituency,constituency_no_data`;

  const { data: pinsData, loading: pinsLoading, error: pinsError } = useFetch<GeoCollection>(pinsPath);
  const { data: polysData, loading: polysLoading } = useFetch<GeoCollection>(polysPath);

  // Loading indicator clears once pins land (map is interactive). Polygons
  // stream in silently after.
  const loading = pinsLoading;
  const error = pinsError;

  const { servers, lines } = useMemo(() => {
    const srv: GeoCollection = { type: "FeatureCollection", features: [] };
    const lns: GeoCollection = { type: "FeatureCollection", features: [] };
    if (!pinsData) return { servers: srv, lines: lns };
    for (const f of pinsData.features) {
      const kind = f.properties.kind;
      if (kind === "server") srv.features.push(f);
      else if (kind === "connection") lns.features.push(f);
    }
    return { servers: srv, lines: lns };
  }, [pinsData]);

  const { polygons, polygonsNoData } = useMemo(() => {
    const polys: GeoCollection = { type: "FeatureCollection", features: [] };
    const polysNo: GeoCollection = { type: "FeatureCollection", features: [] };
    if (!polysData) return { polygons: polys, polygonsNoData: polysNo };
    for (const f of polysData.features) {
      const kind = f.properties.kind;
      if (kind === "constituency") polys.features.push(f);
      else if (kind === "constituency_no_data") polysNo.features.push(f);
    }
    return { polygons: polys, polygonsNoData: polysNo };
  }, [polysData]);

  return (
    <div className="mapview">
      {loading && (
        <div className="mapview__loading" role="status" aria-live="polite">
          <span className="mapview__leaf" aria-hidden>🍁</span>
          <span className="mapview__loading-text">Loading map…</span>
        </div>
      )}
      {!loading && polysLoading && (
        <div className="mapview__loading mapview__loading--subtle" role="status" aria-live="polite">
          <span className="mapview__loading-text">Loading regions…</span>
        </div>
      )}
      {error && <div className="mapview__error">Failed to load: {error.message}</div>}

      <MapContainer
        center={CANADA_CENTER}
        zoom={4}
        minZoom={2}
        style={{ height: "70vh", width: "100%", background: "#0b1220" }}
        preferCanvas
        zoomAnimation={false}
        fadeAnimation={false}
        markerZoomAnimation={false}
        wheelDebounceTime={40}
        wheelPxPerZoomLevel={120}
      >
        <FitToFeatures features={[
          ...polygons.features as GeoJSON.Feature[],
          ...polygonsNoData.features as GeoJSON.Feature[],
        ]} />
        <LayersControl position="topright">
          <LayersControl.BaseLayer checked name="Dark">
            <TileLayer
              attribution='&copy; OpenStreetMap, &copy; CARTO'
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              keepBuffer={4}
              maxNativeZoom={18}
            />
          </LayersControl.BaseLayer>
          <LayersControl.BaseLayer name="Light">
            <TileLayer
              attribution='&copy; OpenStreetMap'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              keepBuffer={4}
            />
          </LayersControl.BaseLayer>

          <LayersControl.Overlay checked name="Constituencies">
            <GeoJSON
              key={`const-${polygons.features.length}`}
              data={polygons as GeoJSON.FeatureCollection}
              style={(f) => {
                const tier = (f?.properties?.worst_tier ?? 6) as SovereigntyTier;
                return {
                  color: TIER_META[tier]?.color ?? "#64748b",
                  weight: 0.5,
                  fillColor: TIER_META[tier]?.color ?? "#64748b",
                  fillOpacity: 0.25,
                };
              }}
              onEachFeature={(f, layer) => {
                const p = f.properties || {};
                layer.bindTooltip(buildConstituencyTooltip(p), { sticky: true });
                layer.bindPopup(buildConstituencyPopup(p), { maxWidth: 340, minWidth: 280 });
                // Hide the hover tooltip while the popup is open so we don't
                // render both info boxes at once.
                layer.on("popupopen", () => layer.closeTooltip());
              }}
            />
          </LayersControl.Overlay>

          {polygonsNoData.features.length > 0 && (
            <LayersControl.Overlay checked name={`Ridings without a website (${polygonsNoData.features.length})`}>
              <GeoJSON
                key={`nodata-${polygonsNoData.features.length}`}
                data={polygonsNoData as GeoJSON.FeatureCollection}
                style={() => ({
                  color: "#475569",
                  weight: 0.7,
                  fillColor: "#1e293b",
                  fillOpacity: 0.45,
                  dashArray: "3 4",
                })}
                onEachFeature={(f, layer) => {
                  const p = f.properties || {};
                  layer.bindTooltip(
                    `<strong>${escapeHtml(String(p.constituency_name ?? ""))}</strong><br>` +
                    `${escapeHtml(String(p.politician_name ?? ""))}${p.party ? ` · ${escapeHtml(String(p.party))}` : ""}<br>` +
                    `<em>No personal/campaign website found</em>`
                  );
                }}
              />
            </LayersControl.Overlay>
          )}

          <LayersControl.Overlay checked name="Connections (Canadian)">
            <GeoJSON
              key={`lines-ca-${lines.features.length}`}
              data={{
                ...lines,
                features: lines.features.filter(
                  (f) => {
                    const t = (f.properties?.sovereignty_tier ?? 6) as SovereigntyTier;
                    return t === 1 || t === 2;
                  }
                ),
              } as GeoJSON.FeatureCollection}
              style={(f) => {
                const tier = (f?.properties?.sovereignty_tier ?? 6) as SovereigntyTier;
                return {
                  color: TIER_META[tier]?.color ?? "#64748b",
                  weight: 0.6,
                  opacity: 0.35,
                  interactive: false,
                };
              }}
            />
          </LayersControl.Overlay>

          <LayersControl.Overlay checked name="Data flow (animated, foreign)">
            <GeoJSON
              key={`flow-${lines.features.length}`}
              data={{
                ...lines,
                features: lines.features.filter((f) => {
                  const t = (f.properties?.sovereignty_tier ?? 6) as SovereigntyTier;
                  return t === 3 || t === 4 || t === 5;
                }),
              } as GeoJSON.FeatureCollection}
              style={(f) => {
                const tier = (f?.properties?.sovereignty_tier ?? 6) as SovereigntyTier;
                return {
                  color: TIER_META[tier]?.color ?? "#94a3b8",
                  weight: 1.6,
                  opacity: 0.8,
                  dashArray: "10 14",
                  lineCap: "round",
                  interactive: false,
                  // Force SVG renderer for THIS layer only so each line is a
                  // real <path> element we can animate via CSS. The map-level
                  // preferCanvas still applies to everything else.
                  renderer: svgFlowRenderer,
                };
              }}
              onEachFeature={(_f, layer) => {
                const lp = layer as L.Path & { _path?: SVGPathElement };
                const apply = () => lp._path?.classList.add("sw-flow-line");
                apply();
                layer.on("add", apply);
              }}
            />
          </LayersControl.Overlay>

          <LayersControl.Overlay checked name="Server locations">
            <GeoJSON
              key={`srv-${servers.features.length}`}
              data={servers as GeoJSON.FeatureCollection}
              pointToLayer={(feature, latlng) => {
                const tier = (feature.properties?.sovereignty_tier ?? 6) as SovereigntyTier;
                const meta = TIER_META[tier];
                return L.circleMarker(latlng, {
                  radius: 5,
                  color: "#0b1220",
                  weight: 1,
                  fillColor: meta.color,
                  fillOpacity: 0.95,
                });
              }}
              onEachFeature={(f, layer) => {
                const p = f.properties || {};
                const title = (p.politician_name ?? p.organization_name ?? p.hostname) as string;
                const tier = (p.sovereignty_tier ?? 6) as SovereigntyTier;
                const meta = TIER_META[tier];
                const cls = String(p.site_class ?? "personal");
                const classBadge = cls === "party_managed"
                  ? `<span class="popup__badge popup__badge--party">PARTY MANAGED</span>`
                  : cls === "personal"
                    ? `<span class="popup__badge popup__badge--personal">PERSONAL</span>`
                    : "";
                layer.bindPopup(
                  `<div class="popup">
                     <div class="popup__title">${escapeHtml(String(title))} ${classBadge}</div>
                     <div class="popup__url"><a href="${encodeURI(String(p.website_url ?? ''))}" target="_blank" rel="noopener">${escapeHtml(String(p.hostname ?? p.website_url ?? ''))}</a></div>
                     <div class="popup__tier" style="color:${meta.color}">${meta.emoji} Tier ${tier} · ${escapeHtml(meta.label)}</div>
                     <div class="popup__row">${escapeHtml(String(p.hosting_provider ?? 'unknown'))} · ${escapeHtml(String(p.city ?? ''))} ${escapeHtml(String(p.hosting_country ?? ''))}</div>
                     ${p.cdn_detected ? `<div class="popup__row">CDN: ${escapeHtml(String(p.cdn_detected))}</div>` : ''}
                     ${p.party ? `<div class="popup__row">Party: ${escapeHtml(String(p.party))}</div>` : ''}
                     ${p.side ? `<div class="popup__row">Side: ${escapeHtml(String(p.side))}</div>` : ''}
                   </div>`
                );
              }}
            />
          </LayersControl.Overlay>
        </LayersControl>
      </MapContainer>
    </div>
  );
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

interface SiteInPopup {
  url: string; hostname: string; label: string | null;
  provider: string | null; country: string | null; city: string | null;
  tier: number;
}

/** Lightweight hover tooltip — politician + constituency + photo + worst-tier */
function buildConstituencyTooltip(p: Record<string, unknown>): string {
  const name = String(p.politician_name ?? "");
  const constituency = String(p.name ?? "");
  const office = String(p.elected_office ?? "");
  const party = String(p.party ?? "");
  const photo = p.photo_url ? String(p.photo_url) : null;
  const tier = (p.worst_tier ?? 6) as SovereigntyTier;
  const meta = TIER_META[tier];
  const sites = Number(p.site_count ?? 0);
  const ca = Number(p.canadian ?? 0);
  return `
    <div class="map-tooltip">
      ${photo ? `<img class="map-tooltip__photo" src="${escapeHtml(photo)}" alt="" loading="lazy"/>` : ""}
      <div class="map-tooltip__body">
        <div class="map-tooltip__name">${escapeHtml(name) || escapeHtml(constituency)}</div>
        <div class="map-tooltip__office">${escapeHtml(office)}${party ? ` · ${escapeHtml(party)}` : ""}</div>
        <div class="map-tooltip__riding">${escapeHtml(constituency)}</div>
        <div class="map-tooltip__tier" style="color:${meta.color}">${meta.emoji} ${escapeHtml(meta.label)}</div>
        <div class="map-tooltip__sites">${ca}/${sites} site${sites === 1 ? "" : "s"} in Canada</div>
      </div>
    </div>`;
}

/** Rich click popup — full politician card with all sites listed */
function buildConstituencyPopup(p: Record<string, unknown>): string {
  const name = String(p.politician_name ?? "");
  const politicianId = p.politician_id ? String(p.politician_id) : null;
  const constituency = String(p.name ?? "");
  const office = String(p.elected_office ?? "");
  const party = String(p.party ?? "");
  const photo = p.photo_url ? String(p.photo_url) : null;
  const sites = (p.sites as SiteInPopup[] | undefined) ?? [];
  const totalSites = sites.length;
  const ca = Number(p.canadian ?? 0);
  const us = Number(p.us ?? 0);
  const cdn = Number(p.cdn ?? 0);

  const siteHtml = sites.map(s => {
    const tier = (s.tier ?? 6) as SovereigntyTier;
    const meta = TIER_META[tier];
    const loc = [s.city, s.country].filter(Boolean).join(", ");
    return `
      <li class="map-popup__site">
        <div class="map-popup__site-host">
          <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.hostname)}</a>
        </div>
        <div class="map-popup__site-meta" style="color:${meta.color}">${meta.emoji} ${escapeHtml(meta.label)}</div>
        <div class="map-popup__site-host-name">${escapeHtml(s.provider ?? "—")}${loc ? ` · ${escapeHtml(loc)}` : ""}</div>
      </li>`;
  }).join("");

  return `
    <div class="map-popup">
      <header class="map-popup__head">
        ${photo ? `<img class="map-popup__photo" src="${escapeHtml(photo)}" alt="" loading="lazy"/>` : ""}
        <div>
          <div class="map-popup__name">${escapeHtml(name) || escapeHtml(constituency)}</div>
          <div class="map-popup__office">${escapeHtml(office)}${party ? ` · ${escapeHtml(party)}` : ""}</div>
          <div class="map-popup__riding">${escapeHtml(constituency)}</div>
        </div>
      </header>
      <div class="map-popup__breakdown">
        <span><strong>${ca}</strong> 🇨🇦 CA</span>
        <span><strong>${cdn}</strong> 🌐 CDN</span>
        <span><strong>${us}</strong> 🇺🇸 US</span>
        <span class="map-popup__total">${totalSites} total</span>
      </div>
      ${totalSites > 0 ? `<ul class="map-popup__sites">${siteHtml}</ul>` : `<p class="map-popup__nosites">No personal/campaign website tracked.</p>`}
      ${politicianId ? `<a class="map-popup__profile" href="/politician/${escapeHtml(politicianId)}">View full profile →</a>` : ""}
    </div>`;
}
