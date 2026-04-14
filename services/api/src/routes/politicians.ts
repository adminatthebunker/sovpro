import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query, queryOne } from "../db.js";

const listQuery = z.object({
  level: z.enum(["federal", "provincial", "municipal"]).optional(),
  province: z.string().length(2).optional(),
  party: z.string().optional(),
  sovereignty_tier: z.coerce.number().int().min(1).max(6).optional(),
  search: z.string().optional(),
  page: z.coerce.number().int().min(1).default(1),
  limit: z.coerce.number().int().min(1).max(500).default(50),
});

// Politician change-tracking filters (Phase 6).
const changesQuery = z.object({
  change_type: z
    .enum([
      "party_switch",
      "office_change",
      "retired",
      "newly_elected",
      "social_added",
      "social_removed",
      "social_dead",
      "constituency_change",
      "name_change",
    ])
    .optional(),
  level: z.enum(["federal", "provincial", "municipal"]).optional(),
  province: z.string().length(2).optional(),
  severity: z.enum(["info", "notable", "major"]).optional(),
  limit: z.coerce.number().int().min(1).max(500).default(50),
});

export default async function politicianRoutes(app: FastifyInstance) {
  // Recent politician-level changes (party switches, retirements, etc.).
  // Registered before /:id so the static path wins routing.
  app.get("/changes", async (req, reply) => {
    const q = changesQuery.safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);
    const { change_type, level, province, severity, limit } = q.data;

    const where: string[] = [];
    const params: (string | number)[] = [];
    if (change_type) { params.push(change_type); where.push(`c.change_type = $${params.length}`); }
    if (level)       { params.push(level);       where.push(`p.level = $${params.length}`); }
    if (province)    { params.push(province);    where.push(`p.province_territory = $${params.length}`); }
    if (severity)    { params.push(severity);    where.push(`c.severity = $${params.length}`); }
    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

    const items = await query(
      `
      SELECT c.id, c.politician_id, c.change_type, c.old_value, c.new_value,
             c.severity, c.detected_at,
             p.name AS politician_name, p.level, p.province_territory,
             p.party, p.elected_office, p.constituency_name, p.is_active
        FROM politician_changes c
        JOIN politicians p ON p.id = c.politician_id
        ${whereSql}
       ORDER BY c.detected_at DESC
       LIMIT ${limit}
      `,
      params,
    );
    return { items, limit };
  });

  // Term history for a single politician.
  app.get("/:id/terms", async (req, reply) => {
    const { id } = req.params as { id: string };
    const pol = await queryOne(
      `SELECT id, name FROM politicians WHERE id = $1`,
      [id],
    );
    if (!pol) return reply.notFound();

    const terms = await query(
      `
      SELECT id, politician_id, office, party, level, province_territory,
             constituency_id, started_at, ended_at, source, created_at
        FROM politician_terms
       WHERE politician_id = $1
       ORDER BY started_at DESC, created_at DESC
      `,
      [id],
    );
    return { politician: pol, terms };
  });

  app.get("/", async (req, reply) => {
    const q = listQuery.safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);
    const { level, province, party, sovereignty_tier, search, page, limit } = q.data;
    const offset = (page - 1) * limit;

    const where: string[] = ["p.is_active = true"];
    const params: (string | number)[] = [];
    if (level)    { params.push(level);    where.push(`p.level = $${params.length}`); }
    if (province) { params.push(province); where.push(`p.province_territory = $${params.length}`); }
    if (party)    { params.push(party);    where.push(`p.party = $${params.length}`); }
    if (search)   { params.push(`%${search}%`); where.push(`p.name ILIKE $${params.length}`); }

    // sovereignty_tier joins latest scan across any of their websites
    let tierJoin = "";
    if (sovereignty_tier) {
      params.push(sovereignty_tier);
      tierJoin = `
        JOIN LATERAL (
            SELECT 1 FROM websites w
            JOIN LATERAL (SELECT * FROM infrastructure_scans WHERE website_id = w.id
                          ORDER BY scanned_at DESC LIMIT 1) s ON true
            WHERE w.owner_type='politician' AND w.owner_id = p.id
              AND s.sovereignty_tier = $${params.length}
            LIMIT 1
        ) sv ON true
      `;
    }

    const countSql = `SELECT COUNT(*)::int AS n FROM politicians p ${tierJoin} WHERE ${where.join(" AND ")}`;
    const total = (await queryOne<{ n: number }>(countSql, params))?.n ?? 0;

    const listSql = `
      SELECT p.*, (SELECT COUNT(*) FROM websites w WHERE w.owner_type='politician' AND w.owner_id=p.id AND w.is_active)::int AS website_count
      FROM politicians p
      ${tierJoin}
      WHERE ${where.join(" AND ")}
      ORDER BY p.last_name NULLS LAST, p.name
      LIMIT ${limit} OFFSET ${offset}
    `;
    const items = await query(listSql, params);

    return { items, page, limit, total, pages: Math.max(1, Math.ceil(total / limit)) };
  });

  app.get("/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    const pol = await queryOne(
      `SELECT * FROM politicians WHERE id = $1 AND is_active = true`, [id]
    );
    if (!pol) return reply.notFound();

    const websites = await query(
      `
      SELECT w.*, s.ip_country, s.ip_city, s.ip_latitude, s.ip_longitude,
             s.hosting_provider, s.hosting_country, s.sovereignty_tier,
             s.cdn_detected, s.cms_detected, s.scanned_at
      FROM websites w
      LEFT JOIN LATERAL (
        SELECT * FROM infrastructure_scans WHERE website_id = w.id
        ORDER BY scanned_at DESC LIMIT 1
      ) s ON true
      WHERE w.owner_type='politician' AND w.owner_id=$1 AND w.is_active=true
      ORDER BY w.label
      `, [id]
    );

    const boundary = (pol as { constituency_id?: string }).constituency_id
      ? await queryOne(
          `SELECT constituency_id, name, level, ST_AsGeoJSON(boundary_simple)::jsonb AS boundary_geojson,
                  ST_X(centroid) AS centroid_lng, ST_Y(centroid) AS centroid_lat
           FROM constituency_boundaries WHERE constituency_id = $1`,
           [(pol as { constituency_id: string }).constituency_id])
      : null;

    return { politician: pol, websites, boundary };
  });
}
