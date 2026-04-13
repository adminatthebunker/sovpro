import { useEffect, useRef } from "react";
import { useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import { antPath } from "leaflet-ant-path";
import type { GeoCollection, SovereigntyTier } from "../types";
import { TIER_META } from "../types";

interface Props {
  data: GeoCollection;
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
export function AntLines({ data }: Props) {
  const map = useMap();
  const layersRef = useRef<AntPathLayer[]>([]);

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
        // Reduced-motion users: render statically (no animation loop)
        paused: PREFERS_REDUCED_MOTION,
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
  }, [data, map]);

  // Pause during interaction OR while tiles are loading — over high-latency
  // links (e.g. Tailscale) tile loads cause SVG reflows that compete with
  // the ant-path setInterval, producing visible flicker.
  useMapEvents({
    zoomstart: () => pauseAll(layersRef.current),
    zoomend:   () => resumeAll(layersRef.current),
    movestart: () => pauseAll(layersRef.current),
    moveend:   () => resumeAll(layersRef.current),
    // @ts-expect-error - loading/load are real Leaflet events not in the type
    loading:   () => pauseAll(layersRef.current),
    load:      () => resumeAll(layersRef.current),
  });

  return null;
}

function pauseAll(layers: AntPathLayer[]) {
  for (const l of layers) { try { l.pause?.(); } catch { /* noop */ } }
}
function resumeAll(layers: AntPathLayer[]) {
  if (PREFERS_REDUCED_MOTION) return;  // never resume if user opted out
  for (const l of layers) { try { l.resume?.(); } catch { /* noop */ } }
}
