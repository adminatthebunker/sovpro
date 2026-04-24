import Stripe from "stripe";
import { config } from "../config.js";
import { query, queryOne } from "../db.js";

/**
 * Stripe billing client — the one place in the codebase that imports
 * from the `stripe` package.
 *
 * This module is the Stripe-swap seam. Consumers import helpers from
 * here, never `stripe` directly. That contains the vendor footprint
 * to one file and makes future concerns (raising a newer API version,
 * switching to Stripe.js direct, etc.) mechanical.
 *
 * The client is lazy-initialised: importing this module when Stripe is
 * unconfigured is safe — the error only surfaces when a caller tries
 * to use a function that needs the SDK. Routes should guard with
 * `isConfigured()` first and 503 gracefully.
 */

// Don't pin apiVersion in code. Hand-pinning a literal (e.g.
// "2026-03-25.dahlia") goes stale as soon as the SDK publishes a
// newer dahlia snapshot — each `stripe` npm version accepts exactly
// one literal, and a mismatch between local node_modules and the
// Docker build's node_modules breaks the build.
//
// The actual pin that matters in production is the `stripe` package
// version in package.json (no caret — exact version). Bumping that
// is the deliberate, reviewable act; the SDK's bundled apiVersion
// moves with it. When upgrading, re-read Stripe's API changelog for
// any breaking changes to checkout.session.completed.
let cachedClient: Stripe | null = null;

export function isConfigured(): boolean {
  return config.stripe.enabled;
}

function getClient(): Stripe {
  if (!config.stripe.secretKey) {
    throw new Error("STRIPE_SECRET_KEY not configured");
  }
  if (!cachedClient) {
    cachedClient = new Stripe(config.stripe.secretKey, {
      typescript: true,
    });
  }
  return cachedClient;
}

/**
 * Credit-pack catalog. Each pack maps a Stripe price id (set in env)
 * to the credit amount granted on successful checkout. The credit
 * amount lives server-side so a client cannot claim "I bought 700
 * credits" — the webhook handler looks up the authoritative value
 * from this table using the price id on the session.
 *
 * Round-3 planning locks the final credit counts; until then these
 * are starter-proposal values. Changing the credit_amount here is a
 * code-only change — existing purchases are unaffected since the
 * credits_granted is snapshotted into credit_purchases at purchase
 * time.
 */
export interface CreditPack {
  sku: "small" | "medium" | "large";
  priceId: string;
  credits: number;
  displayPrice: string;
  bonusLabel?: string;
}

const PACK_CREDITS: Record<CreditPack["sku"], number> = {
  small: 50,
  medium: 250,   // +50 over linear → 12.5% bonus vs. small at scale
  large: 700,    // +200 over linear → 17% bonus vs. small at scale
};

const PACK_DISPLAY: Record<CreditPack["sku"], { price: string; bonus?: string }> = {
  small: { price: "$5" },
  medium: { price: "$20", bonus: "12% bonus" },
  large: { price: "$50", bonus: "17% bonus" },
};

export function getAvailablePacks(): CreditPack[] {
  const packs: CreditPack[] = [];
  for (const sku of ["small", "medium", "large"] as const) {
    const priceId = config.stripe.priceIds[sku];
    if (!priceId) continue;
    packs.push({
      sku,
      priceId,
      credits: PACK_CREDITS[sku],
      displayPrice: PACK_DISPLAY[sku].price,
      bonusLabel: PACK_DISPLAY[sku].bonus,
    });
  }
  return packs;
}

export function getPackByPriceId(priceId: string): CreditPack | null {
  const packs = getAvailablePacks();
  return packs.find((p) => p.priceId === priceId) ?? null;
}

export function getPackBySku(sku: string): CreditPack | null {
  const packs = getAvailablePacks();
  return packs.find((p) => p.sku === sku) ?? null;
}

/**
 * Upsert a Stripe customer for the given user. If `users.stripe_customer_id`
 * is already set, returns it unchanged. Otherwise creates a new customer
 * on Stripe and persists the id on the row.
 *
 * Two consumers: the credit-pack checkout flow and (future) the dev-API
 * subscription flow. Both share one Stripe customer per user.
 */
export async function getOrCreateCustomer(userId: string, email: string): Promise<string> {
  const existing = await queryOne<{ stripe_customer_id: string | null }>(
    `SELECT stripe_customer_id FROM users WHERE id = $1`,
    [userId]
  );
  if (existing?.stripe_customer_id) {
    return existing.stripe_customer_id;
  }

  const customer = await getClient().customers.create({
    email,
    metadata: { user_id: userId },
  });

  await query(
    `UPDATE users SET stripe_customer_id = $1 WHERE id = $2 AND stripe_customer_id IS NULL`,
    [customer.id, userId]
  );

  // Race: if another request raced past the SELECT above, both would
  // create Stripe customers and one UPDATE would no-op (WHERE clause).
  // Re-read to return the winning id — the losing Stripe customer is
  // orphaned but harmless (no charges until a checkout uses it).
  const winner = await queryOne<{ stripe_customer_id: string | null }>(
    `SELECT stripe_customer_id FROM users WHERE id = $1`,
    [userId]
  );
  return winner?.stripe_customer_id ?? customer.id;
}

/**
 * Create a one-time-payment Stripe Checkout Session for a credit pack.
 *
 * `clientReferenceId` is set to the user id so the webhook handler can
 * map session → user even if Stripe's customer metadata is sparse.
 * `metadata.user_id` + `metadata.sku` belt-and-braces the same.
 *
 * `payment_intent_data.metadata` propagates to the underlying
 * PaymentIntent — useful for forensic lookups in the Stripe dashboard
 * when a user's purchase needs investigation.
 */
export async function createCheckoutSession(params: {
  userId: string;
  userEmail: string;
  sku: CreditPack["sku"];
}): Promise<{ url: string; sessionId: string }> {
  const pack = getPackBySku(params.sku);
  if (!pack) throw new Error(`unknown or unavailable credit pack: ${params.sku}`);

  const customerId = await getOrCreateCustomer(params.userId, params.userEmail);

  const session = await getClient().checkout.sessions.create({
    mode: "payment",
    customer: customerId,
    client_reference_id: params.userId,
    line_items: [{ price: pack.priceId, quantity: 1 }],
    success_url: config.stripe.successUrl,
    cancel_url: config.stripe.cancelUrl,
    metadata: {
      user_id: params.userId,
      sku: pack.sku,
      credits: String(pack.credits),
    },
    payment_intent_data: {
      metadata: {
        user_id: params.userId,
        sku: pack.sku,
        credits: String(pack.credits),
      },
    },
  });

  if (!session.url) {
    throw new Error("stripe returned session without url");
  }
  return { url: session.url, sessionId: session.id };
}

/**
 * Verify a webhook payload's signature and return the parsed event.
 * Throws if the signature is invalid — callers should 400 on throw,
 * never 200 (otherwise Stripe stops retrying and we lose events).
 *
 * `rawBody` must be the unparsed request body as received from
 * Stripe. Fastify's default JSON parser mutates the body, so the
 * webhook route registers a content-type override that preserves the
 * raw bytes. See services/api/src/routes/stripe-webhook.ts.
 */
export function constructWebhookEvent(
  rawBody: string | Buffer,
  signature: string
): Stripe.Event {
  if (!config.stripe.webhookSecret) {
    throw new Error("STRIPE_WEBHOOK_SECRET not configured");
  }
  return getClient().webhooks.constructEvent(
    rawBody,
    signature,
    config.stripe.webhookSecret
  );
}
