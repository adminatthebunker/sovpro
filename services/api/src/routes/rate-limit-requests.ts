import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query, queryOne } from "../db.js";
import { requireUser, getUser } from "../middleware/user-auth.js";
import { requireCsrf } from "../lib/csrf.js";

/**
 * User-facing rate-limit increase request endpoint.
 *
 * The admin side of this flow already exists in routes/admin.ts
 * (GET /admin/rate-limit-requests, PATCH /admin/rate-limit-requests/:id).
 * This is the user-submission half: a signed-in user who has hit
 * their default rate limit can explain their use case and ask the
 * admin to bump them to `extended` or `unlimited`.
 *
 * Registered at prefix /me/rate-limit-requests. Three endpoints:
 *   GET  /       — the user's own requests (all statuses, newest first)
 *   POST /       — submit a new request (one-pending-at-a-time)
 */

const submitBody = z.object({
  reason: z.string().trim().min(10).max(2000),
  requested_tier: z.enum(["extended", "unlimited"]),
});

interface OwnRequestRow {
  id: string;
  reason: string;
  requested_tier: "extended" | "unlimited";
  status: "pending" | "approved" | "denied";
  admin_response: string | null;
  created_at: string;
  resolved_at: string | null;
}

export default async function rateLimitRequestRoutes(app: FastifyInstance) {
  // ── GET /me/rate-limit-requests ─────────────────────────────
  app.get("/", { preHandler: requireUser }, async (req, reply) => {
    const claims = getUser(req);
    if (!claims) return reply.code(401).send({ error: "not signed in" });

    const rows = await query<OwnRequestRow>(
      `SELECT id, reason, requested_tier, status, admin_response,
              created_at, resolved_at
         FROM rate_limit_increase_requests
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT 20`,
      [claims.sub]
    );
    return reply.send({ requests: rows });
  });

  // ── POST /me/rate-limit-requests ────────────────────────────
  // Tight per-route rate limit: limit submission spam. A legitimate
  // user submits this once per issue; abuse is the only reason to
  // submit many in a minute.
  app.post(
    "/",
    {
      preHandler: [requireUser, requireCsrf],
      config: { rateLimit: { max: 3, timeWindow: "1 hour" } },
    },
    async (req, reply) => {
      const claims = getUser(req);
      if (!claims) return reply.code(401).send({ error: "not signed in" });

      const parsed = submitBody.safeParse(req.body);
      if (!parsed.success) {
        return reply.code(400).send({
          error: "invalid body",
          details: parsed.error.flatten(),
        });
      }

      // One-pending-at-a-time guard. The admin queue shouldn't fill
      // with duplicate requests from the same user while they wait
      // for a decision. Enforced at the app layer (no DB unique index
      // to keep this fix in-scope without another migration); if spam
      // becomes a concern, a partial unique index on
      // (user_id) WHERE status = 'pending' moves this to the DB.
      const existing = await queryOne<{ id: string }>(
        `SELECT id FROM rate_limit_increase_requests
          WHERE user_id = $1 AND status = 'pending'
          LIMIT 1`,
        [claims.sub]
      );
      if (existing) {
        return reply.code(409).send({
          error: "you already have a pending rate-limit increase request",
          existing_id: existing.id,
        });
      }

      const row = await queryOne<OwnRequestRow>(
        `INSERT INTO rate_limit_increase_requests
             (user_id, reason, requested_tier, status)
           VALUES ($1, $2, $3, 'pending')
           RETURNING id, reason, requested_tier, status, admin_response,
                     created_at, resolved_at`,
        [claims.sub, parsed.data.reason, parsed.data.requested_tier]
      );
      if (!row) return reply.code(500).send({ error: "insert returned no row" });
      return reply.code(201).send(row);
    }
  );
}
