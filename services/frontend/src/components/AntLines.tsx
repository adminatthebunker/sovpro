import { useEffect, useRef } from "react";
import { useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import { antPath } from "leaflet-ant-path";
import type { GeoCollection, SovereigntyTier } from "../types";
import { TIER_META } from "../types";

interface Props {
  data: GeoCollection;
  /** When true, render paths as static (no animation loop). */
  paused?: boolean;
}

interface AntPathLayer extends L.Polyline {
  pause?: () => void;
  resume?: () => void;
  isPaused?: () => boolean;
}

/** True when the user has reduced-motion enabled — we skip animation entirely. */
const PREFERS_REDUCED_MOTION =
  typeof window !== "undefined" &&
  window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Animated "ant-path" polylines for foreign-tier connection features.
 *
 * Flicker mitigation: leaflet-ant-path runs its own setInterval to update
 * dashOffset on the SVG stroke. Mid-zoom that fights Leaflet's transform
 * pipeline. We pause every animation on zoomstart and resume on zoomend.
 */
export function AntLines({ data, paused = false }: Props) {
  const map = useMap();
  const layersRef = useRef<AntPathLayer[]>([]);
  const wantPaused = paused || PREFERS_REDUCED_MOTION;

  useEffect(() => {
    for (const l of layersRef.current) {
      try { map.removeLayer(l); } catch { /* noop */ }
    }
    layersRef.current = [];

    for (const f of data.features) {
      if (f.properties?.kind !== "connection") continue;
      const tier = (f.properties.sovereignty_tier ?? 6) as SovereigntyTier;
      if (tier !== 3 && tier !== 4 && tier !== 5) continue;
      const geom = f.geometry as { type: string; coordinates: [number, number][] };
      if (geom?.type !== "LineString" || geom.coordinates.length < 2) continue;
      const meta = TIER_META[tier];
      const latlngs = geom.coordinates.map(([lng, lat]) => [lat, lng] as [number, number]);

      const path = antPath(latlngs, {
        delay: 800,
        dashArray: [10, 20],
        weight: 1.4,
        color: meta.color,
        pulseColor: "#fafafa",
        opacity: 0.7,
        paused: wantPaused,
        reverse: false,
        hardwareAccelerated: true,
        interactive: false,
      }) as AntPathLayer;
      path.addTo(map);
      layersRef.current.push(path);
    }

    return () => {
      for (const l of layersRef.current) {
        try { map.removeLayer(l); } catch { /* noop */ }
      }
      layersRef.current = [];
    };
  }, [data, map, wantPaused]);

  // Pause only on user-initiated zoom/pan. Removed the per-tile-load
  // listeners — over high-latency links those fire constantly and the
  // pause/resume thrash itself caused flicker.
  useMapEvents({
    zoomstart: () => pauseAll(layersRef.current),
    zoomend:   () => !wantPaused && resumeAll(layersRef.current),
    movestart: () => pauseAll(layersRef.current),
    moveend:   () => !wantPaused && resumeAll(layersRef.current),
  });

  return null;
}

function pauseAll(layers: AntPathLayer[]) {
  for (const l of layers) { try { l.pause?.(); } catch { /* noop */ } }
}
function resumeAll(layers: AntPathLayer[]) {
  for (const l of layers) { try { l.resume?.(); } catch { /* noop */ } }
}
