import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query } from "../db.js";
import type { MapRow } from "../types.js";

const geoQuery = z.object({
  level: z.enum(["federal","provincial","municipal"]).optional(),
  province: z.string().length(2).optional(),
  party: z.string().optional(),
  group: z.enum(["politicians","organizations","all"]).default("all"),
  include_no_data: z.coerce.boolean().default(false),
});

type GeoFeature = {
  type: "Feature";
  id?: string;
  properties: Record<string, unknown>;
  geometry: unknown;
};

type GeoCollection = { type: "FeatureCollection"; features: GeoFeature[] };

export default async function mapRoutes(app: FastifyInstance) {
  app.get("/geojson", async (req, reply) => {
    const q = geoQuery.safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);
    const { level, province, party, group, include_no_data } = q.data;

    const features: GeoFeature[] = [];

    // ── Politicians layer (constituency polygons + server pins + connection lines) ──
    if (group === "politicians" || group === "all") {
      const where: string[] = [];
      const params: (string | number)[] = [];
      if (level)    { params.push(level);    where.push(`level = $${params.length}`); }
      if (province) { params.push(province); where.push(`province_territory = $${params.length}`); }
      if (party)    { params.push(party);    where.push(`party = $${params.length}`); }
      const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

      const rows = await query<MapRow>(
        `SELECT * FROM map_politicians ${whereSql}`,
        params
      );

      // Group by constituency so we only emit one polygon per district.
      const emittedConst = new Set<string>();
      for (const row of rows) {
        if (row.boundary_geojson && row.constituency_id && !emittedConst.has(row.constituency_id)) {
          emittedConst.add(row.constituency_id);
          features.push({
            type: "Feature",
            id: `const-${row.constituency_id}`,
            properties: {
              kind: "constituency",
              constituency_id: row.constituency_id,
              name: row.constituency_name,
              level: row.level,
              province: row.province_territory,
              worst_tier: row.sovereignty_tier ?? 6,
            },
            geometry: row.boundary_geojson,
          });
        }

        if (row.server_lat != null && row.server_lng != null) {
          features.push({
            type: "Feature",
            id: `srv-${row.website_id}`,
            properties: {
              kind: "server",
              owner_type: "politician",
              politician_id: row.politician_id,
              politician_name: row.name,
              party: row.party,
              constituency_name: row.constituency_name,
              website_id: row.website_id,
              website_url: row.website_url,
              hostname: row.hostname,
              hosting_provider: row.hosting_provider,
              hosting_country: row.hosting_country,
              city: row.ip_city,
              sovereignty_tier: row.sovereignty_tier,
              cdn_detected: row.cdn_detected,
            },
            geometry: { type: "Point", coordinates: [row.server_lng, row.server_lat] },
          });

          if (row.constituency_lat != null && row.constituency_lng != null) {
            features.push({
              type: "Feature",
              id: `line-${row.website_id}`,
              properties: {
                kind: "connection",
                owner_type: "politician",
                politician_id: row.politician_id,
                website_id: row.website_id,
                sovereignty_tier: row.sovereignty_tier,
              },
              geometry: {
                type: "LineString",
                coordinates: [
                  [row.constituency_lng, row.constituency_lat],
                  [row.server_lng, row.server_lat],
                ],
              },
            });
          }
        }
      }

      // ── Constituencies with NO scanned website (no-data overlay) ─────
      if (include_no_data) {
        const noDataWhere: string[] = ["p.is_active = true",
          `NOT EXISTS (SELECT 1 FROM map_politicians mp WHERE mp.politician_id = p.id)`];
        const ndParams: (string | number)[] = [];
        if (level)    { ndParams.push(level);    noDataWhere.push(`p.level = $${ndParams.length}`); }
        if (province) { ndParams.push(province); noDataWhere.push(`p.province_territory = $${ndParams.length}`); }
        if (party)    { ndParams.push(party);    noDataWhere.push(`p.party = $${ndParams.length}`); }

        const noDataRows = await query<{
          politician_id: string; name: string; party: string | null; level: string;
          constituency_id: string; constituency_name: string | null;
          boundary_geojson: unknown;
        }>(
          `SELECT p.id AS politician_id, p.name, p.party, p.level,
                  cb.constituency_id, p.constituency_name,
                  ST_AsGeoJSON(cb.boundary_simple)::jsonb AS boundary_geojson
           FROM politicians p
           JOIN constituency_boundaries cb ON cb.constituency_id = p.constituency_id
           WHERE ${noDataWhere.join(" AND ")}`,
          ndParams
        );
        for (const r of noDataRows) {
          if (!r.boundary_geojson) continue;
          features.push({
            type: "Feature",
            id: `no-data-${r.constituency_id}`,
            properties: {
              kind: "constituency_no_data",
              constituency_id: r.constituency_id,
              constituency_name: r.constituency_name,
              politician_name: r.name,
              party: r.party,
              level: r.level,
            },
            geometry: r.boundary_geojson,
          });
        }
      }
    }

    // ── Organizations layer ──────────────────────────────────────
    if (group === "organizations" || group === "all") {
      const where: string[] = [];
      const params: (string | number)[] = [];
      if (province) { params.push(province); where.push(`province_territory = $${params.length}`); }
      const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

      const rows = await query<MapRow>(
        `SELECT * FROM map_organizations ${whereSql}`, params
      );

      for (const row of rows) {
        if (row.server_lat != null && row.server_lng != null) {
          features.push({
            type: "Feature",
            id: `org-${row.website_id}`,
            properties: {
              kind: "server",
              owner_type: "organization",
              organization_id: row.organization_id,
              organization_name: row.name,
              org_type: row.type,
              side: row.side,
              website_id: row.website_id,
              website_url: row.website_url,
              hostname: row.hostname,
              hosting_provider: row.hosting_provider,
              hosting_country: row.hosting_country,
              city: row.ip_city,
              sovereignty_tier: row.sovereignty_tier,
              cdn_detected: row.cdn_detected,
            },
            geometry: { type: "Point", coordinates: [row.server_lng, row.server_lat] },
          });
        }
      }
    }

    const fc: GeoCollection = { type: "FeatureCollection", features };
    reply.header("cache-control", "public, max-age=60");
    return fc;
  });

  // Referendum-focused view: leave vs stay orgs with context boundary
  app.get("/referendum", async (_req, reply) => {
    const rows = await query<MapRow>(
      `SELECT * FROM map_organizations
       WHERE type IN ('referendum_leave','referendum_stay') OR side IN ('leave','stay')`
    );

    const features: GeoFeature[] = [];
    for (const row of rows) {
      if (row.server_lat != null && row.server_lng != null) {
        features.push({
          type: "Feature",
          id: `ref-${row.website_id}`,
          properties: {
            kind: "server",
            side: row.side,
            organization_id: row.organization_id,
            organization_name: row.name,
            org_type: row.type,
            website_url: row.website_url,
            hostname: row.hostname,
            hosting_provider: row.hosting_provider,
            hosting_country: row.hosting_country,
            city: row.ip_city,
            sovereignty_tier: row.sovereignty_tier,
            cdn_detected: row.cdn_detected,
          },
          geometry: { type: "Point", coordinates: [row.server_lng, row.server_lat] },
        });
      }
    }

    // Alberta boundary as context — union all AB provincial boundaries
    const abRow = await query<{ geojson: unknown }>(
      `SELECT ST_AsGeoJSON(ST_Union(boundary_simple))::jsonb AS geojson
       FROM constituency_boundaries WHERE province_territory = 'AB' AND level = 'provincial'`
    );
    if (abRow[0]?.geojson) {
      features.unshift({
        type: "Feature",
        id: "ab-boundary",
        properties: { kind: "context_boundary", region: "Alberta" },
        geometry: abRow[0].geojson,
      });
    }

    reply.header("cache-control", "public, max-age=120");
    return { type: "FeatureCollection", features };
  });
}
