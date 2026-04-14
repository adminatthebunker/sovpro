import type { FastifyInstance } from "fastify";
import { query, queryOne } from "../db.js";

// ────────────────────────────────────────────────────────────────
// /api/v1/socials/stats
// /api/v1/socials/politicians/:id
// ────────────────────────────────────────────────────────────────
//
// Phase 5 — surfaces the politician_socials table:
//   - stats: aggregate counts by platform + liveness buckets
//   - per-politician: the ordered list of their normalized handles
//
// Route conventions match politicians.ts (no external validator for
// read-only endpoints).
export default async function socialsRoutes(app: FastifyInstance) {
  app.get("/stats", async () => {
    const byPlatform = await query<{ platform: string; n: number }>(
      `SELECT platform, COUNT(*)::int AS n
         FROM politician_socials
         GROUP BY platform
         ORDER BY n DESC`
    );

    const totals = await queryOne<{
      total: number;
      live: number;
      dead: number;
      never_verified: number;
    }>(
      `SELECT
          COUNT(*)::int                                           AS total,
          COUNT(*) FILTER (WHERE is_live = true)::int             AS live,
          COUNT(*) FILTER (WHERE is_live = false)::int            AS dead,
          COUNT(*) FILTER (WHERE last_verified_at IS NULL)::int   AS never_verified
         FROM politician_socials`
    );

    return {
      by_platform: Object.fromEntries(byPlatform.map(r => [r.platform, r.n])),
      total: totals?.total ?? 0,
      live: totals?.live ?? 0,
      dead: totals?.dead ?? 0,
      never_verified: totals?.never_verified ?? 0,
    };
  });

  app.get("/politicians/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    const exists = await queryOne<{ n: number }>(
      `SELECT 1 AS n FROM politicians WHERE id = $1`, [id]
    );
    if (!exists) return reply.notFound();

    const items = await query(
      `SELECT id, politician_id, platform, handle, url,
              last_verified_at, is_live, follower_count,
              created_at, updated_at
         FROM politician_socials
        WHERE politician_id = $1
        ORDER BY platform, handle`,
      [id]
    );

    return { items };
  });
}
