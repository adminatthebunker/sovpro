declare module "leaflet-ant-path" {
  import * as L from "leaflet";
  export interface AntPathOptions extends L.PolylineOptions {
    delay?: number;
    dashArray?: number[];
    weight?: number;
    color?: string;
    pulseColor?: string;
    opacity?: number;
    paused?: boolean;
    reverse?: boolean;
    hardwareAccelerated?: boolean;
    interactive?: boolean;
  }
  export function antPath(
    latlngs: L.LatLngExpression[] | L.LatLngExpression[][],
    options?: AntPathOptions
  ): L.Polyline;
}
