import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query, queryOne } from "../db.js";
import { requireUser, getUser } from "../middleware/user-auth.js";
import { SESSION_COOKIE } from "../lib/auth-token.js";
import { CSRF_COOKIE, requireCsrf } from "../lib/csrf.js";
import { baseFilterSchema, encodeQuery, toPgVector } from "./search.js";
import { feedToken } from "./feeds.js";
import { config } from "../config.js";

/**
 * Self-service user routes. Everything here is gated by requireUser.
 * Mutating endpoints additionally require the double-submit CSRF token.
 *
 * Scope in task #3 (this file as shipped): GET /me, POST /me/logout,
 * PATCH /me (display_name). Saved-searches CRUD lands next in task #4
 * and will extend this same register() function.
 */

interface UserRow {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
  last_login_at: string | null;
}

const patchBody = z.object({
  display_name: z.string().trim().max(100).nullable().optional(),
});

// Saved-search create/update shapes. filter_payload reuses the
// baseFilterSchema from /search so "what can be saved" is always
// identical to "what can be searched" — one source of truth.
const savedSearchCreateBody = z.object({
  name: z.string().trim().min(1).max(100),
  filter_payload: baseFilterSchema,
  alert_cadence: z.enum(["none", "daily", "weekly"]).default("none"),
});

const savedSearchPatchBody = z.object({
  name: z.string().trim().min(1).max(100).optional(),
  alert_cadence: z.enum(["none", "daily", "weekly"]).optional(),
});

interface SavedSearchRow {
  id: string;
  user_id: string;
  name: string;
  filter_payload: z.infer<typeof baseFilterSchema>;
  alert_cadence: "none" | "daily" | "weekly";
  last_checked_at: string | null;
  last_notified_at: string | null;
  created_at: string;
  updated_at: string;
  has_embedding: boolean;
}

interface SavedSearchResponse extends SavedSearchRow {
  feed_url: string | null;
}

function withFeedUrl(row: SavedSearchRow): SavedSearchResponse {
  const tok = feedToken(row.id);
  return {
    ...row,
    feed_url: tok ? `${config.publicSiteUrl}/api/v1/feeds/${tok}.rss` : null,
  };
}

export default async function meRoutes(app: FastifyInstance) {
  // ── GET /me ──────────────────────────────────────────────────
  app.get("/", { preHandler: requireUser }, async (req, reply) => {
    const claims = getUser(req);
    if (!claims) return reply.code(401).send({ error: "not signed in" });

    const row = await queryOne<UserRow>(
      `SELECT id, email, display_name, created_at, last_login_at
         FROM users WHERE id = $1`,
      [claims.sub]
    );
    if (!row) {
      // Session is valid but the user row is gone — treat as logged out.
      reply.clearCookie(SESSION_COOKIE, { path: "/" });
      reply.clearCookie(CSRF_COOKIE, { path: "/" });
      return reply.code(401).send({ error: "account no longer exists" });
    }
    return reply.send(row);
  });

  // ── PATCH /me ────────────────────────────────────────────────
  app.patch("/", { preHandler: [requireUser, requireCsrf] }, async (req, reply) => {
    const claims = getUser(req);
    if (!claims) return reply.code(401).send({ error: "not signed in" });

    const parsed = patchBody.safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid body" });
    }
    const { display_name } = parsed.data;

    const rows = await query<UserRow>(
      `UPDATE users SET display_name = $1 WHERE id = $2
       RETURNING id, email, display_name, created_at, last_login_at`,
      [display_name ?? null, claims.sub]
    );
    if (!rows[0]) return reply.code(404).send({ error: "user not found" });
    return reply.send(rows[0]);
  });

  // ── POST /me/logout ──────────────────────────────────────────
  // Logout does NOT require CSRF: a forged cross-site logout is
  // low-impact (the user just has to sign in again) and requiring CSRF
  // means a stale token prevents a user from recovering their session.
  app.post("/logout", { preHandler: requireUser }, async (_req, reply) => {
    reply.clearCookie(SESSION_COOKIE, { path: "/" });
    reply.clearCookie(CSRF_COOKIE, { path: "/" });
    return reply.code(204).send();
  });

  // ── GET /me/saved-searches ───────────────────────────────────
  app.get("/saved-searches", { preHandler: requireUser }, async (req, reply) => {
    const claims = getUser(req);
    if (!claims) return reply.code(401).send({ error: "not signed in" });

    const rows = await query<SavedSearchRow>(
      `SELECT id, user_id, name, filter_payload, alert_cadence,
              last_checked_at, last_notified_at, created_at, updated_at,
              (query_embedding IS NOT NULL) AS has_embedding
         FROM saved_searches
        WHERE user_id = $1
        ORDER BY created_at DESC`,
      [claims.sub]
    );
    return reply.send({ saved_searches: rows.map(withFeedUrl) });
  });

  // ── GET /me/saved-searches/:id ───────────────────────────────
  app.get<{ Params: { id: string } }>(
    "/saved-searches/:id",
    { preHandler: requireUser },
    async (req, reply) => {
      const claims = getUser(req);
      if (!claims) return reply.code(401).send({ error: "not signed in" });

      const row = await queryOne<SavedSearchRow>(
        `SELECT id, user_id, name, filter_payload, alert_cadence,
                last_checked_at, last_notified_at, created_at, updated_at,
                (query_embedding IS NOT NULL) AS has_embedding
           FROM saved_searches
          WHERE id = $1 AND user_id = $2`,
        [req.params.id, claims.sub]
      );
      if (!row) return reply.code(404).send({ error: "not found" });
      return reply.send(withFeedUrl(row));
    }
  );

  // ── POST /me/saved-searches ──────────────────────────────────
  app.post(
    "/saved-searches",
    { preHandler: [requireUser, requireCsrf] },
    async (req, reply) => {
      const claims = getUser(req);
      if (!claims) return reply.code(401).send({ error: "not signed in" });

      const parsed = savedSearchCreateBody.safeParse(req.body);
      if (!parsed.success) {
        return reply.code(400).send({ error: "invalid body", details: parsed.error.flatten() });
      }
      const { name, filter_payload, alert_cadence } = parsed.data;

      // Embed the query now so the alerts worker never has to call TEI.
      // A filter without q (pure time/politician/party filter) gets no
      // embedding — alerts for that kind of search should rely on the
      // filter alone, not semantic ranking.
      let embeddingLiteral: string | null = null;
      if (filter_payload.q && filter_payload.q.trim().length > 0) {
        try {
          const vec = await encodeQuery(filter_payload.q);
          embeddingLiteral = toPgVector(vec);
        } catch (err) {
          req.log.error({ err }, "[saved-searches] TEI embed failed");
          // Save without an embedding rather than 500 — the user's search
          // still functions via the /search endpoint. Alerts accuracy
          // will be lower, which is acceptable degradation.
        }
      }

      const rows = await query<SavedSearchRow>(
        `INSERT INTO saved_searches
            (user_id, name, filter_payload, query_embedding, alert_cadence)
         VALUES ($1, $2, $3::jsonb, $4::vector, $5)
         RETURNING id, user_id, name, filter_payload, alert_cadence,
                   last_checked_at, last_notified_at, created_at, updated_at,
                   (query_embedding IS NOT NULL) AS has_embedding`,
        [
          claims.sub,
          name,
          JSON.stringify(filter_payload),
          embeddingLiteral,
          alert_cadence,
        ]
      );
      if (!rows[0]) return reply.code(500).send({ error: "insert failed" });
      return reply.code(201).send(withFeedUrl(rows[0]));
    }
  );

  // ── PATCH /me/saved-searches/:id ─────────────────────────────
  app.patch<{ Params: { id: string } }>(
    "/saved-searches/:id",
    { preHandler: [requireUser, requireCsrf] },
    async (req, reply) => {
      const claims = getUser(req);
      if (!claims) return reply.code(401).send({ error: "not signed in" });

      const parsed = savedSearchPatchBody.safeParse(req.body);
      if (!parsed.success) {
        return reply.code(400).send({ error: "invalid body" });
      }
      const { name, alert_cadence } = parsed.data;

      // Dynamic UPDATE: only touch columns the caller sent. Keeps the
      // touch_updated_at trigger honest (no-op PATCHes don't stamp).
      const sets: string[] = [];
      const params: (string | null)[] = [];
      if (name !== undefined) {
        params.push(name);
        sets.push(`name = $${params.length}`);
      }
      if (alert_cadence !== undefined) {
        params.push(alert_cadence);
        sets.push(`alert_cadence = $${params.length}`);
      }
      if (sets.length === 0) {
        return reply.code(400).send({ error: "no fields to update" });
      }
      params.push(req.params.id);
      params.push(claims.sub);

      const rows = await query<SavedSearchRow>(
        `UPDATE saved_searches SET ${sets.join(", ")}
          WHERE id = $${params.length - 1} AND user_id = $${params.length}
         RETURNING id, user_id, name, filter_payload, alert_cadence,
                   last_checked_at, last_notified_at, created_at, updated_at,
                   (query_embedding IS NOT NULL) AS has_embedding`,
        params
      );
      if (!rows[0]) return reply.code(404).send({ error: "not found" });
      return reply.send(withFeedUrl(rows[0]));
    }
  );

  // ── DELETE /me/saved-searches/:id ────────────────────────────
  app.delete<{ Params: { id: string } }>(
    "/saved-searches/:id",
    { preHandler: [requireUser, requireCsrf] },
    async (req, reply) => {
      const claims = getUser(req);
      if (!claims) return reply.code(401).send({ error: "not signed in" });

      const res = await query<{ id: string }>(
        `DELETE FROM saved_searches WHERE id = $1 AND user_id = $2 RETURNING id`,
        [req.params.id, claims.sub]
      );
      if (res.length === 0) return reply.code(404).send({ error: "not found" });
      return reply.code(204).send();
    }
  );
}
