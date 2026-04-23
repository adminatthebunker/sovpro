import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { queryOne } from "../db.js";
import { requireUser, getUser } from "../middleware/user-auth.js";
import { requireCsrf } from "../lib/csrf.js";
import {
  isConfigured as stripeIsConfigured,
  getAvailablePacks,
  createCheckoutSession,
} from "../lib/stripe.js";
import { getBalance, listLedgerEntries } from "../lib/credits.js";

/**
 * User-facing credits routes. All gated on requireUser; mutating paths
 * additionally require the double-submit CSRF token.
 *
 * Registered at prefix /me/credits by services/api/src/index.ts so the
 * shapes here are GET /, GET /packs, POST /checkout.
 *
 * The Stripe SDK is touched only inside POST /checkout (lazy-
 * initialised via lib/stripe.ts). GET /packs is safe to call even with
 * Stripe unconfigured — it returns an empty array, which the frontend
 * renders as "no packs available yet."
 */

const checkoutBody = z.object({
  sku: z.enum(["small", "medium", "large"]),
});

interface UserEmailRow {
  email: string;
}

export default async function creditsRoutes(app: FastifyInstance) {
  // ── GET /me/credits ─────────────────────────────────────────
  // Balance + recent ledger history. A single round-trip to the
  // DB per call (one balance query + one history query — both on
  // the user_id index, both cheap).
  app.get("/", { preHandler: requireUser }, async (req, reply) => {
    const claims = getUser(req);
    if (!claims) return reply.code(401).send({ error: "not signed in" });

    const [balance, history] = await Promise.all([
      getBalance(claims.sub),
      listLedgerEntries(claims.sub, 50),
    ]);

    // Strip reference_id from the user-facing response shape. For
    // stripe_purchase rows it's the cs_live_... / cs_test_... checkout
    // session id, which is not a secret but not useful to the user
    // either; for report_hold rows it's a report_jobs.id that leaks
    // nothing. The admin endpoint (/admin/users/:id) retains the raw
    // reference_id for support purposes.
    const safeHistory = history.map(({ reference_id: _drop, ...rest }) => rest);

    return reply.send({
      balance,
      history: safeHistory,
      stripe_enabled: stripeIsConfigured(),
    });
  });

  // ── GET /me/credits/packs ───────────────────────────────────
  // Public pack listing — what the user can buy right now. Filtered
  // to packs whose STRIPE_PRICE_ID_* is set. No Stripe SDK call; all
  // info lives in config.
  app.get("/packs", { preHandler: requireUser }, async (_req, reply) => {
    const packs = getAvailablePacks().map((p) => ({
      sku: p.sku,
      credits: p.credits,
      display_price: p.displayPrice,
      bonus_label: p.bonusLabel ?? null,
    }));
    return reply.send({
      enabled: stripeIsConfigured(),
      packs,
    });
  });

  // ── POST /me/credits/checkout ───────────────────────────────
  // Creates a Stripe Checkout Session and returns the hosted-page
  // URL. Frontend window.location.assigns the URL to redirect the
  // user into Stripe's flow.
  //
  // Tight per-route rate limit: each call creates a real Stripe API
  // session (billable request + dashboard pollution). Global 300/min
  // is too loose for this endpoint. Five/min lets a confused user
  // retry a few times without locking them out.
  app.post(
    "/checkout",
    {
      preHandler: [requireUser, requireCsrf],
      config: { rateLimit: { max: 5, timeWindow: "1 minute" } },
    },
    async (req, reply) => {
      if (!stripeIsConfigured()) {
        return reply.code(503).send({ error: "stripe not configured" });
      }

      const parsed = checkoutBody.safeParse(req.body);
      if (!parsed.success) {
        return reply.code(400).send({ error: "invalid body", details: parsed.error.flatten() });
      }

      const claims = getUser(req);
      if (!claims) return reply.code(401).send({ error: "not signed in" });

      // Email is needed for Stripe customer creation. The JWT carries
      // an email claim, but re-reading from the DB keeps the source
      // of truth on the user row (handles edge cases like email
      // change mid-session).
      const row = await queryOne<UserEmailRow>(
        `SELECT email FROM users WHERE id = $1`,
        [claims.sub]
      );
      if (!row) return reply.code(404).send({ error: "user not found" });

      try {
        const { url, sessionId } = await createCheckoutSession({
          userId: claims.sub,
          userEmail: row.email,
          sku: parsed.data.sku,
        });
        req.log.info({ user_id: claims.sub, sku: parsed.data.sku, sessionId }, "[credits] checkout session created");
        return reply.send({ url, session_id: sessionId });
      } catch (err) {
        req.log.error({ err, user_id: claims.sub, sku: parsed.data.sku }, "[credits] checkout creation failed");
        return reply.code(502).send({ error: "failed to create checkout session" });
      }
    }
  );
}
