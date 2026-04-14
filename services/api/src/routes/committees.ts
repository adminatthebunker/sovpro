import type { FastifyInstance } from "fastify";
import { query, queryOne } from "../db.js";

// ────────────────────────────────────────────────────────────────
// /api/v1/committees
// /api/v1/committees/:name/members
// ────────────────────────────────────────────────────────────────
//
// Surfaces the politician_committees table populated by the scanner's
// committees.py module. Filters:
//   level     = 'federal' | 'provincial' | 'municipal'
//   province  = AB | BC | ON | ...
//   include_past  (members endpoint) = 1/true to include closed memberships
//
// Read-only; no request body validators in keeping with the other
// routes in this service.
export default async function committeesRoutes(app: FastifyInstance) {
  app.get("/", async (req) => {
    const q = req.query as { level?: string; province?: string };
    const filters: string[] = ["pc.ended_at IS NULL"];
    const params: (string | number)[] = [];
    if (q.level) {
      params.push(q.level);
      filters.push(`pc.level = $${params.length}`);
    }
    if (q.province) {
      params.push(q.province);
      filters.push(`p.province_territory = $${params.length}`);
    }

    const rows = await query<{
      committee_name: string;
      level: string | null;
      source: string | null;
      member_count: number;
      chairs: number;
      vice_chairs: number;
    }>(
      `SELECT
          pc.committee_name,
          MAX(pc.level)       AS level,
          MAX(pc.source)      AS source,
          COUNT(*)::int       AS member_count,
          COUNT(*) FILTER (WHERE pc.role ILIKE 'chair'
                              OR pc.role ILIKE 'joint chair')::int AS chairs,
          COUNT(*) FILTER (WHERE pc.role ILIKE 'vice-chair'
                              OR pc.role ILIKE 'vice chair')::int  AS vice_chairs
         FROM politician_committees pc
         JOIN politicians p ON p.id = pc.politician_id
         WHERE ${filters.join(" AND ")}
         GROUP BY pc.committee_name
         ORDER BY member_count DESC, pc.committee_name ASC`,
      params,
    );

    return { items: rows, count: rows.length };
  });

  app.get("/:name/members", async (req, reply) => {
    const { name } = req.params as { name: string };
    const q = req.query as { include_past?: string };
    const includePast =
      q.include_past === "1"
      || (q.include_past ?? "").toLowerCase() === "true";

    const exists = await queryOne<{ n: number }>(
      `SELECT 1 AS n FROM politician_committees
         WHERE committee_name = $1 LIMIT 1`,
      [name],
    );
    if (!exists) return reply.notFound();

    const extra = includePast ? "" : "AND pc.ended_at IS NULL";

    const items = await query<{
      membership_id: string;
      politician_id: string;
      name: string;
      party: string | null;
      province_territory: string | null;
      constituency_name: string | null;
      role: string | null;
      level: string | null;
      source: string | null;
      started_at: string | null;
      ended_at: string | null;
    }>(
      `SELECT
          pc.id                AS membership_id,
          p.id                 AS politician_id,
          p.name,
          p.party,
          p.province_territory,
          p.constituency_name,
          pc.role,
          pc.level,
          pc.source,
          pc.started_at,
          pc.ended_at
         FROM politician_committees pc
         JOIN politicians p ON p.id = pc.politician_id
         WHERE pc.committee_name = $1 ${extra}
         ORDER BY
           CASE
             WHEN pc.role ILIKE 'chair' THEN 0
             WHEN pc.role ILIKE 'joint chair' THEN 0
             WHEN pc.role ILIKE 'vice%' THEN 1
             WHEN pc.role ILIKE 'deputy%' THEN 1
             ELSE 2
           END,
           p.name ASC`,
      [name],
    );

    return { committee_name: name, count: items.length, items };
  });
}
