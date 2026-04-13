import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query, queryOne } from "../db.js";

const q = z.object({
  since: z.string().datetime().optional(),
  owner_type: z.enum(["politician","organization"]).optional(),
  change_type: z.string().optional(),
  severity: z.enum(["info","notable","major"]).optional(),
  page: z.coerce.number().int().min(1).default(1),
  limit: z.coerce.number().int().min(1).max(200).default(25),
});

export default async function changesRoutes(app: FastifyInstance) {
  app.get("/", async (req, reply) => {
    const p = q.safeParse(req.query);
    if (!p.success) return reply.badRequest(p.error.message);
    const { since, owner_type, change_type, severity, page, limit } = p.data;
    const offset = (page - 1) * limit;

    const where: string[] = [];
    const params: (string | number)[] = [];
    if (since)       { params.push(since);       where.push(`c.detected_at >= $${params.length}`); }
    if (owner_type)  { params.push(owner_type);  where.push(`w.owner_type = $${params.length}`); }
    if (change_type) { params.push(change_type); where.push(`c.change_type = $${params.length}`); }
    if (severity)    { params.push(severity);    where.push(`c.severity = $${params.length}`); }
    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

    const total = (await queryOne<{ n: number }>(
      `SELECT COUNT(*)::int AS n FROM scan_changes c JOIN websites w ON w.id = c.website_id ${whereSql}`,
      params
    ))?.n ?? 0;

    const items = await query(
      `
      SELECT c.*, w.url AS website_url, w.owner_type, w.owner_id,
             CASE w.owner_type
               WHEN 'politician'   THEN (SELECT name FROM politicians    WHERE id = w.owner_id)
               WHEN 'organization' THEN (SELECT name FROM organizations  WHERE id = w.owner_id)
             END AS owner_name
      FROM scan_changes c
      JOIN websites w ON w.id = c.website_id
      ${whereSql}
      ORDER BY c.detected_at DESC
      LIMIT ${limit} OFFSET ${offset}
      `,
      params
    );

    return { items, page, limit, total, pages: Math.max(1, Math.ceil(total / limit)) };
  });
}
