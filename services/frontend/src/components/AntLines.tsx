import { useEffect } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import { antPath } from "leaflet-ant-path";
import type { GeoCollection, SovereigntyTier } from "../types";
import { TIER_META } from "../types";

interface Props {
  data: GeoCollection;
}

/**
 * Renders a layer of animated "ant-path" polylines for each connection feature.
 * Each line's color and animation speed encodes the sovereignty tier of the
 * destination server: foreign hosts get faster, brighter flow to suggest data
 * leaving Canada.
 */
export function AntLines({ data }: Props) {
  const map = useMap();

  useEffect(() => {
    const layers: L.Layer[] = [];

    for (const f of data.features) {
      if (f.properties?.kind !== "connection") continue;
      const geom = f.geometry as { type: string; coordinates: [number, number][] };
      if (geom?.type !== "LineString" || geom.coordinates.length < 2) continue;
      const tier = (f.properties.sovereignty_tier ?? 6) as SovereigntyTier;
      const meta = TIER_META[tier];
      // Foreign tiers (3,4,5) animate faster + brighter to suggest data leakage
      const isForeign = tier === 3 || tier === 4 || tier === 5;
      // [lng, lat] -> [lat, lng]
      const latlngs = geom.coordinates.map(([lng, lat]) => [lat, lng] as [number, number]);

      const path = antPath(latlngs, {
        delay: isForeign ? 700 : 1800,
        dashArray: [10, 20],
        weight: isForeign ? 1.5 : 0.7,
        color: meta.color,
        pulseColor: isForeign ? "#fafafa" : meta.color,
        opacity: isForeign ? 0.65 : 0.25,
        paused: false,
        reverse: false,
        hardwareAccelerated: true,
        interactive: false,
      });
      path.addTo(map);
      layers.push(path);
    }

    return () => {
      for (const l of layers) {
        try { map.removeLayer(l); } catch { /* noop */ }
      }
    };
  }, [data, map]);

  return null;
}
