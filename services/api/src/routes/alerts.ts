import type { FastifyInstance } from "fastify";
import { createHmac, timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { config } from "../config.js";
import { query } from "../db.js";

/**
 * Unauthenticated, token-gated alert-management endpoints.
 *
 * The token itself is the auth: it's an HMAC-SHA256 over
 *   "unsubscribe:" + <saved_search_id>
 * using JWT_SECRET as the key. Anyone who received an alert email for a
 * saved search has the token for THAT saved search, and only that one.
 * The 'unsubscribe:' prefix binds the signature to this purpose even
 * though we share JWT_SECRET with the RSS feed tokens — a feed token
 * can never be replayed as an unsubscribe token (different message).
 *
 * RFC-8058 requires that the POST variant work *without* the user being
 * logged in — the HMAC token IS the authorization — and that the action
 * be idempotent. A stale link (saved search already deleted or already
 * unsubscribed) still responds 200 so the user-facing outcome
 * ("I'm unsubscribed") is true either way.
 */

const querySchema = z.object({ t: z.string().regex(/^[0-9a-f]{64}$/) });

function verifyToken(token: string, savedSearchId: string): boolean {
  if (!config.jwtSecret) return false;
  const expected = createHmac("sha256", config.jwtSecret)
    .update(`unsubscribe:${savedSearchId}`)
    .digest();
  let received: Buffer;
  try {
    received = Buffer.from(token, "hex");
  } catch {
    return false;
  }
  if (received.length !== expected.length) return false;
  return timingSafeEqual(expected, received);
}

/**
 * To unsubscribe, we need to know which saved_search_id the token was
 * signed for — but the token doesn't carry the id, only the MAC. So we
 * fetch the candidate IDs (just the subscribed ones) and check each.
 * At phase-1 scale this is O(N) saved_searches with cadence != 'none',
 * which is trivial; if we ever have >10k active alert subscribers we'd
 * flip the token format to `<id>:<hmac>` and cut the scan.
 */
async function findSavedSearchByToken(token: string): Promise<string | null> {
  if (!config.jwtSecret) return null;
  const rows = await query<{ id: string }>(
    `SELECT id FROM saved_searches WHERE alert_cadence <> 'none'`
  );
  for (const row of rows) {
    if (verifyToken(token, row.id)) return row.id;
  }
  return null;
}

async function applyUnsubscribe(savedSearchId: string): Promise<void> {
  await query(
    `UPDATE saved_searches SET alert_cadence = 'none' WHERE id = $1`,
    [savedSearchId]
  );
}

export default async function alertRoutes(app: FastifyInstance) {
  const routeConfig = {
    config: {
      rateLimit: { max: 30, timeWindow: "1 minute" },
    },
  };

  // ── GET /alerts/unsubscribe?t=<token> ────────────────────────
  // Human-clickable. Flips cadence to 'none' and redirects to the user's
  // saved-searches page with a confirmation flag.
  app.get("/unsubscribe", routeConfig, async (req, reply) => {
    const parsed = querySchema.safeParse(req.query);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid token" });
    }
    const target = await findSavedSearchByToken(parsed.data.t);
    if (target) {
      await applyUnsubscribe(target);
    }
    // Idempotent: even on stale/unknown tokens, land the user on the
    // management page rather than surfacing a 400.
    const url = `${config.publicSiteUrl}/account/saved-searches?unsubscribed=1`;
    return reply.redirect(url, 302);
  });

  // ── POST /alerts/unsubscribe ─────────────────────────────────
  // RFC-8058 one-click. Gmail/Outlook POST here when the user clicks
  // the inbox-provider "Unsubscribe" button. Must respond 200 without
  // redirection.
  app.post("/unsubscribe", routeConfig, async (req, reply) => {
    // Gmail sends the token in the query string per the List-Unsubscribe
    // header URL. We also accept it in the body for clients that prefer
    // form-encoded POST.
    const qParsed = querySchema.safeParse(req.query);
    let token: string | null = qParsed.success ? qParsed.data.t : null;
    if (!token) {
      const bodyParsed = querySchema.safeParse(req.body);
      if (bodyParsed.success) token = bodyParsed.data.t;
    }
    if (!token) {
      return reply.code(400).send({ error: "invalid token" });
    }
    const target = await findSavedSearchByToken(token);
    if (target) {
      await applyUnsubscribe(target);
    }
    return reply.code(200).send({ ok: true });
  });
}
