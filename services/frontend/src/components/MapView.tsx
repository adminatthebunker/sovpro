import { useMemo } from "react";
import { MapContainer, TileLayer, GeoJSON, LayersControl, Marker, Popup, CircleMarker } from "react-leaflet";
import L from "leaflet";
import type { GeoCollection, SovereigntyTier } from "../types";
import { TIER_META } from "../types";
import { useFetch } from "../hooks/useFetch";
import type { FilterState } from "./Filters";

interface Props {
  filters: FilterState;
}

const CANADA_CENTER: [number, number] = [56.1304, -106.3468];

// Fix default marker icons under bundlers
// @ts-expect-error - leaflet icon fallback
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  iconUrl:       "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  shadowUrl:     "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
});

export function MapView({ filters }: Props) {
  const path = useMemo(() => {
    const params = new URLSearchParams();
    if (filters.level) params.set("level", filters.level);
    if (filters.province) params.set("province", filters.province);
    params.set("group", filters.layer === "all" ? "all" : filters.layer);
    return `/map/geojson?${params.toString()}`;
  }, [filters]);

  const { data, loading, error } = useFetch<GeoCollection>(path);

  const { polygons, servers, lines } = useMemo(() => {
    const polys: typeof data = { type: "FeatureCollection", features: [] };
    const srv:  typeof data = { type: "FeatureCollection", features: [] };
    const lns:  typeof data = { type: "FeatureCollection", features: [] };
    if (!data) return { polygons: polys, servers: srv, lines: lns };
    for (const f of data.features) {
      const kind = f.properties.kind;
      if (kind === "constituency") polys.features.push(f);
      else if (kind === "server") srv.features.push(f);
      else if (kind === "connection") lns.features.push(f);
    }
    return { polygons: polys, servers: srv, lines: lns };
  }, [data]);

  return (
    <div className="mapview">
      {loading && <div className="mapview__loading">Loading map…</div>}
      {error && <div className="mapview__error">Failed to load: {error.message}</div>}

      <MapContainer
        center={CANADA_CENTER}
        zoom={4}
        minZoom={2}
        style={{ height: "70vh", width: "100%", background: "#0b1220" }}
        preferCanvas
      >
        <LayersControl position="topright">
          <LayersControl.BaseLayer checked name="Dark">
            <TileLayer
              attribution='&copy; OpenStreetMap, &copy; CARTO'
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            />
          </LayersControl.BaseLayer>
          <LayersControl.BaseLayer name="Light">
            <TileLayer
              attribution='&copy; OpenStreetMap'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
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
                layer.bindTooltip(
                  `<strong>${escapeHtml(String(p.name ?? ""))}</strong><br>` +
                  `${escapeHtml(String(p.level ?? ""))} · ${escapeHtml(String(p.province ?? ""))}`
                );
              }}
            />
          </LayersControl.Overlay>

          <LayersControl.Overlay checked name="Connections">
            <GeoJSON
              key={`lines-${lines.features.length}`}
              data={lines as GeoJSON.FeatureCollection}
              style={(f) => {
                const tier = (f?.properties?.sovereignty_tier ?? 6) as SovereigntyTier;
                return {
                  color: TIER_META[tier]?.color ?? "#64748b",
                  weight: 0.8,
                  opacity: 0.4,
                };
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
                layer.bindPopup(
                  `<div class="popup">
                     <div class="popup__title">${escapeHtml(String(title))}</div>
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
