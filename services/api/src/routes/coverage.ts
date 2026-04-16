import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query } from "../db.js";

// GET /api/v1/coverage
//
// Public coverage dashboard feed — reads jurisdiction_sources and
// returns one row per Canadian jurisdiction we do (or don't) cover.
// Seeded by db/migrations/0019_jurisdiction_sources.sql; row counts
// (bills_count, speeches_count, etc.) are refreshed by a separate job
// against the live bills / speeches tables.
//
// The frontend renders a table grouped by `bills_status`. No filtering
// by default — coverage is small (14 rows), so we serve the whole set.
// Optional `?status=live` for future use (e.g. a "what's live" widget
// on the lander).

const listQuery = z.object({
  status: z.enum(["live", "partial", "blocked", "none"]).optional(),
});

interface CoverageRow {
  jurisdiction: string;
  legislature_name: string;
  seats: number | null;
  bills_status: string;
  hansard_status: string;
  votes_status: string;
  committees_status: string;
  bills_difficulty: number | null;
  hansard_difficulty: number | null;
  votes_difficulty: number | null;
  committees_difficulty: number | null;
  blockers: string | null;
  notes: string | null;
  source_urls: Record<string, unknown>;
  bills_count: number;
  speeches_count: number;
  votes_count: number;
  politicians_count: number;
  last_verified_at: string | null;
  updated_at: string;
}

export default async function coverageRoutes(app: FastifyInstance) {
  app.get("/", async (req, reply) => {
    const parsed = listQuery.safeParse(req.query);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const { status } = parsed.data;

    const rows = await query<CoverageRow>(
      `SELECT jurisdiction, legislature_name, seats,
              bills_status, hansard_status, votes_status, committees_status,
              bills_difficulty, hansard_difficulty, votes_difficulty, committees_difficulty,
              blockers, notes, source_urls,
              bills_count, speeches_count, votes_count, politicians_count,
              last_verified_at, updated_at
         FROM jurisdiction_sources
        ${status ? "WHERE bills_status = $1" : ""}
        ORDER BY
          CASE jurisdiction
            WHEN 'federal' THEN 0
            ELSE 1
          END,
          jurisdiction`,
      status ? [status] : []
    );

    // Rollup counts — convenient for the page header without a second
    // round-trip. "live" counts anything with a live bills pipeline.
    const summary = {
      total: rows.length,
      live: rows.filter(r => r.bills_status === "live").length,
      partial: rows.filter(r => r.bills_status === "partial").length,
      blocked: rows.filter(r => r.bills_status === "blocked").length,
      none: rows.filter(r => r.bills_status === "none").length,
    };

    return { jurisdictions: rows, summary };
  });
}
