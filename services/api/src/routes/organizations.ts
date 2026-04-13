import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query, queryOne } from "../db.js";

const listQuery = z.object({
  type: z.enum([
    "referendum_leave","referendum_stay","political_party",
    "indigenous_rights","advocacy","government_body","media",
  ]).optional(),
  side: z.enum(["leave","stay","neutral"]).optional(),
  search: z.string().optional(),
  page: z.coerce.number().int().min(1).default(1),
  limit: z.coerce.number().int().min(1).max(200).default(50),
});

export default async function organizationRoutes(app: FastifyInstance) {
  app.get("/", async (req, reply) => {
    const q = listQuery.safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);
    const { type, side, search, page, limit } = q.data;
    const offset = (page - 1) * limit;

    const where: string[] = ["is_active = true"];
    const params: (string | number)[] = [];
    if (type)   { params.push(type); where.push(`type = $${params.length}`); }
    if (side)   { params.push(side); where.push(`side = $${params.length}`); }
    if (search) { params.push(`%${search}%`); where.push(`name ILIKE $${params.length}`); }

    const total = (await queryOne<{ n: number }>(
      `SELECT COUNT(*)::int AS n FROM organizations WHERE ${where.join(" AND ")}`, params
    ))?.n ?? 0;

    const items = await query(
      `SELECT *, (SELECT COUNT(*) FROM websites w WHERE w.owner_type='organization' AND w.owner_id=organizations.id AND w.is_active)::int AS website_count
       FROM organizations WHERE ${where.join(" AND ")}
       ORDER BY type, name
       LIMIT ${limit} OFFSET ${offset}`,
      params
    );

    return { items, page, limit, total, pages: Math.max(1, Math.ceil(total / limit)) };
  });

  app.get("/:idOrSlug", async (req, reply) => {
    const { idOrSlug } = req.params as { idOrSlug: string };
    const isUuid = /^[0-9a-f-]{32,36}$/i.test(idOrSlug);
    const org = await queryOne(
      isUuid
        ? `SELECT * FROM organizations WHERE id = $1 AND is_active = true`
        : `SELECT * FROM organizations WHERE slug = $1 AND is_active = true`,
      [idOrSlug]
    );
    if (!org) return reply.notFound();

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
      WHERE w.owner_type='organization' AND w.owner_id=$1 AND w.is_active=true
      ORDER BY w.label
      `, [(org as { id: string }).id]
    );

    return { organization: org, websites };
  });
}
