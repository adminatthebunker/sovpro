-- Guard against mis-projected boundaries silently landing in the table.
--
-- Symptom: Fort Erie (ON) ward boundaries were fetched from Open North in
-- NAD83 / UTM 17N (EPSG:26917) — easting/northing in meters, not lat/lng —
-- but ingested as SRID 4326 without reprojection. Centroids landed at
-- (~660000, ~4750000), so the frontend drew connection lines from the
-- riding centroid to Microsoft Azure Boydton that extended past the edge
-- of the world.
--
-- Data repair (idempotent — rows already in bounds pass through):
UPDATE constituency_boundaries
   SET boundary = ST_Transform(ST_SetSRID(boundary, 26917), 4326)
 WHERE source_set = 'fort-erie-wards'
   AND level = 'municipal'
   AND (ST_XMin(boundary) < -180 OR ST_XMax(boundary) > 180
     OR ST_YMin(boundary) <  -90 OR ST_YMax(boundary) >  90);

-- Recompute derived columns (simplified geom, centroid, area) for any row
-- we just fixed. Safe to run across the whole table — recomputes are
-- deterministic.
UPDATE constituency_boundaries
   SET boundary_simple = ST_Multi(
         ST_CollectionExtract(
           ST_MakeValid(ST_Simplify(boundary, 0.005)), 3)),
       centroid = ST_Centroid(boundary),
       area_sqkm = ST_Area(boundary::geography)/1000000,
       updated_at = now()
 WHERE source_set = 'fort-erie-wards' AND level = 'municipal';

-- Hard guard so this class of bug cannot recur — any insert whose geometry
-- extends past valid WGS84 bounds fails loudly at the DB.
ALTER TABLE constituency_boundaries
  DROP CONSTRAINT IF EXISTS boundary_in_wgs84_bounds;
ALTER TABLE constituency_boundaries
  ADD CONSTRAINT boundary_in_wgs84_bounds
  CHECK (ST_XMin(boundary) >= -180 AND ST_XMax(boundary) <= 180
     AND ST_YMin(boundary) >=  -90 AND ST_YMax(boundary) <=  90);

-- Refresh the materialized views so the connection endpoints on the map
-- pick up the corrected centroids.
REFRESH MATERIALIZED VIEW map_politicians;
