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
        paused: false,
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

  useMapEvents({
    zoomstart: () => {
      for (const l of layersRef.current) {
        try { l.pause?.(); } catch { /* noop */ }
      }
    },
    zoomend: () => {
      for (const l of layersRef.current) {
        try { l.resume?.(); } catch { /* noop */ }
      }
    },
    movestart: () => {
      for (const l of layersRef.current) {
        try { l.pause?.(); } catch { /* noop */ }
      }
    },
    moveend: () => {
      for (const l of layersRef.current) {
        try { l.resume?.(); } catch { /* noop */ }
      }
    },
  });

  return null;
}
