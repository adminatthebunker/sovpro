import type { FastifyInstance } from "fastify";
import type Stripe from "stripe";
import { query } from "../db.js";
import {
  isConfigured as stripeIsConfigured,
  constructWebhookEvent,
  getPackBySku,
} from "../lib/stripe.js";
import { grantStripePurchase } from "../lib/credits.js";

/**
 * Stripe webhook handler — POST /webhooks/stripe.
 *
 * Plugin-scoped raw-body parser: Stripe signs the raw request bytes,
 * so Fastify's default JSON body parser would break verification by
 * reserialising. Fastify's encapsulation scopes the content-type
 * parser below to routes registered inside this plugin only — other
 * routes continue to receive parsed JSON objects.
 *
 * Two idempotency layers cooperate here:
 *   1. `stripe_webhook_events` PK on event.id drops duplicate
 *      deliveries at the front door. Second receipt of an event we've
 *      already processed succeeds (return 200, no-op).
 *   2. `uniq_credit_ledger_kind_ref` on (kind, reference_id) is the
 *      belt-and-braces layer: even if the event table check somehow
 *      let a dupe through, a single Stripe checkout cannot grant
 *      credits twice.
 *
 * We ALWAYS return 200 on successfully-ingested events — including
 * events we choose to ignore (unrecognised types). Stripe retries on
 * non-2xx for up to 3 days; returning non-200 for "I don't care about
 * this event type" would burn their retry queue.
 *
 * On signature failure we return 400: the request isn't Stripe, so
 * there's nothing to retry. The raw-body check happens BEFORE any DB
 * write; unsigned / malformed events never touch the ledger.
 */

export default async function stripeWebhookRoutes(app: FastifyInstance) {
  // Raw-body JSON parser scoped to this plugin only. Fastify's
  // encapsulation means routes registered outside this function still
  // get the default application/json parser.
  app.addContentTypeParser(
    "application/json",
    { parseAs: "buffer" },
    (_req, body, done) => {
      done(null, body);
    }
  );

  app.post("/", async (req, reply) => {
    if (!stripeIsConfigured()) {
      // Return 200 (not 5xx) so Stripe stops retrying while we're
      // misconfigured. Stripe retries 5xx responses for up to 72
      // hours, which would burn through their retry budget without
      // any hope of success. The operator-facing warning lives in
      // config.ts's startup log, not here.
      req.log.warn({}, "[stripe-webhook] received event but Stripe not configured; discarding");
      return reply.code(200).send({ received: false, reason: "stripe not configured" });
    }

    const signature = req.headers["stripe-signature"];
    if (!signature || typeof signature !== "string") {
      return reply.code(400).send({ error: "missing stripe-signature header" });
    }

    const rawBody = req.body as Buffer;
    if (!Buffer.isBuffer(rawBody)) {
      req.log.error({ type: typeof req.body }, "[stripe-webhook] raw body parser did not produce a Buffer");
      return reply.code(500).send({ error: "internal body parser error" });
    }

    let event: Stripe.Event;
    try {
      event = constructWebhookEvent(rawBody, signature);
    } catch (err) {
      req.log.warn({ err }, "[stripe-webhook] signature verification failed");
      return reply.code(400).send({ error: "signature verification failed" });
    }

    // Upstream dedup: record the event id. PK violation means we've
    // already seen this event — return 200 without reprocessing.
    try {
      await query(
        `INSERT INTO stripe_webhook_events (id, type, raw_payload)
             VALUES ($1, $2, $3)`,
        [event.id, event.type, JSON.stringify(event)]
      );
    } catch (err) {
      // Duplicate PK is the expected happy-path for retries.
      const code = (err as { code?: string }).code;
      if (code === "23505") {
        req.log.info({ event_id: event.id, type: event.type }, "[stripe-webhook] duplicate event, already processed");
        return reply.send({ received: true, duplicate: true });
      }
      req.log.error({ err, event_id: event.id }, "[stripe-webhook] failed to record event");
      return reply.code(500).send({ error: "failed to record event" });
    }

    try {
      await dispatchEvent(req, event);
      await query(
        `UPDATE stripe_webhook_events SET processed_at = now() WHERE id = $1`,
        [event.id]
      );
      return reply.send({ received: true });
    } catch (err) {
      // Record the failure so the admin can investigate without
      // losing the event payload. Return non-200 to trigger Stripe's
      // retry — the PK prevents duplicate rows, but retries let us
      // recover after a transient DB / upstream failure.
      const message = err instanceof Error ? err.message : String(err);
      await query(
        `UPDATE stripe_webhook_events SET error_message = $2 WHERE id = $1`,
        [event.id, message.slice(0, 1000)]
      );
      req.log.error({ err, event_id: event.id, type: event.type }, "[stripe-webhook] handler failed");
      return reply.code(500).send({ error: "handler failed" });
    }
  });
}

async function dispatchEvent(
  req: { log: { info: (obj: object, msg: string) => void; warn: (obj: object, msg: string) => void } },
  event: Stripe.Event
): Promise<void> {
  switch (event.type) {
    case "checkout.session.completed":
      await handleCheckoutCompleted(req, event);
      return;

    // Ignored but acknowledged 200 so Stripe stops retrying. When
    // phase 2 adds subscriptions (dev-API plan), those event types
    // get their own case here.
    default:
      req.log.info({ type: event.type, id: event.id }, "[stripe-webhook] ignoring event type");
      return;
  }
}

async function handleCheckoutCompleted(
  req: { log: { info: (obj: object, msg: string) => void; warn: (obj: object, msg: string) => void } },
  event: Stripe.Event
): Promise<void> {
  const session = event.data.object as Stripe.Checkout.Session;

  // One-time payment packs only — subscription checkouts land through
  // a different handler (not yet written).
  if (session.mode !== "payment") {
    req.log.info({ mode: session.mode, session_id: session.id }, "[stripe-webhook] ignoring non-payment checkout");
    return;
  }

  const userId = session.client_reference_id ?? session.metadata?.user_id;
  if (!userId) {
    req.log.warn({ session_id: session.id }, "[stripe-webhook] checkout session missing user_id");
    throw new Error(`session ${session.id} has no user reference`);
  }

  // SECURITY: the credit amount MUST come from the server-side catalog
  // keyed on the session's sku, not from session.metadata.credits. A
  // Stripe Dashboard operator can edit pending-session metadata freely
  // (Stripe signs the event after delivery, so signature validation
  // does NOT protect against tampered metadata). We read the sku from
  // metadata, then look up the authoritative credit count from
  // PACK_CREDITS server-side. Any mismatch between the two is logged
  // and the catalog value wins. An unknown sku hard-fails — we never
  // guess.
  const metadataSku = session.metadata?.sku;
  if (!metadataSku) {
    throw new Error(`session ${session.id} missing sku metadata`);
  }
  const pack = getPackBySku(metadataSku);
  if (!pack) {
    throw new Error(`session ${session.id} has unknown sku: ${metadataSku}`);
  }
  const credits = pack.credits;

  // Informational: detect tampering. The metadata field has no
  // authority over the grant, but a mismatch here is a strong signal
  // that someone edited a session in the Stripe dashboard.
  const metadataCredits = session.metadata?.credits;
  if (metadataCredits !== undefined) {
    const parsed = Number.parseInt(metadataCredits, 10);
    if (Number.isFinite(parsed) && parsed !== pack.credits) {
      req.log.warn(
        {
          session_id: session.id,
          metadata_credits: parsed,
          catalog_credits: pack.credits,
          sku: metadataSku,
        },
        "[stripe-webhook] metadata credits disagrees with catalog — using catalog (possible dashboard tamper)"
      );
    }
  }

  const amountCents = session.amount_total ?? 0;
  const currency = session.currency ?? "cad";
  const paymentIntentId =
    typeof session.payment_intent === "string" ? session.payment_intent : null;

  try {
    await grantStripePurchase({
      userId,
      stripeCheckoutId: session.id,
      stripePaymentIntentId: paymentIntentId,
      amountCents,
      currency,
      credits,
      rawWebhook: event,
    });
    req.log.info(
      { user_id: userId, session_id: session.id, credits },
      "[stripe-webhook] credits granted"
    );
  } catch (err) {
    // Downstream dedup layer fired: the ledger already has a row for
    // this checkout id. Treat as success — the purchase landed on an
    // earlier delivery.
    const code = (err as { code?: string }).code;
    if (code === "23505") {
      req.log.info(
        { session_id: session.id },
        "[stripe-webhook] ledger already has entry for this checkout, skipping"
      );
      return;
    }
    throw err;
  }
}

/**
 * Ensure we also see the validation type for the checkout ignored
 * branches above. Exporting nothing runtime-visible, just to keep the
 * tsc diagnostic surface honest.
 */
export type { Stripe };
