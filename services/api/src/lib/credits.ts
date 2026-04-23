import { pool, query, queryOne } from "../db.js";
import type Stripe from "stripe";

/**
 * Credit-ledger helpers.
 *
 * The ledger in `credit_ledger` is append-only-with-state-transitions:
 * once a row is inserted, its delta and kind never change. The only
 * mutation is `state: 'held' → 'committed'` (report succeeded) or
 * `'held' → 'refunded'` (report failed). Balance is always derived
 * from SUM(delta) WHERE state IN ('committed','held') — there is no
 * cached balance column anywhere in the system.
 *
 * Two idempotency layers cooperate here:
 *
 *   1. The unique partial index `uniq_credit_ledger_kind_ref` on
 *      (kind, reference_id) means a single Stripe checkout or a
 *      single report_jobs.id can only produce one ledger row per
 *      kind. The webhook handler relies on this to survive duplicate
 *      deliveries without writing check-then-insert code.
 *
 *   2. The state-flip helpers (commitHold, releaseHold) accept any
 *      sequence of calls on a already-finalised row — the flip is a
 *      no-op when the row isn't in 'held' state. That makes worker
 *      retries safe: if a worker crashes between finalising the
 *      report and committing the hold, the retry commits normally.
 *
 * All public functions return small plain objects, never raw SQL
 * rows. Consumers should never need to know ledger column names.
 */

export interface LedgerEntry {
  id: string;
  delta: number;
  state: "pending" | "held" | "committed" | "refunded";
  kind:
    | "stripe_purchase"
    | "admin_credit"
    | "report_hold"
    | "report_commit"
    | "report_refund";
  reference_id: string | null;
  reason: string | null;
  created_at: Date;
}

/**
 * Spendable balance for a user: grants (+) plus active holds (-).
 * Refunded rows drop out; pending rows are excluded until they settle.
 */
export async function getBalance(userId: string): Promise<number> {
  const row = await queryOne<{ balance: string | null }>(
    `SELECT COALESCE(SUM(delta), 0)::text AS balance
       FROM credit_ledger
      WHERE user_id = $1
        AND state IN ('committed','held')`,
    [userId]
  );
  return Number(row?.balance ?? 0);
}

/**
 * Recent ledger history for a user — powers the /account/credits
 * history table. Capped at `limit` rows; caller paginates client-side.
 */
export async function listLedgerEntries(
  userId: string,
  limit = 50
): Promise<LedgerEntry[]> {
  return query<LedgerEntry>(
    `SELECT id, delta, state, kind, reference_id, reason, created_at
       FROM credit_ledger
      WHERE user_id = $1
      ORDER BY created_at DESC
      LIMIT $2`,
    [userId, limit]
  );
}

/**
 * Grant credits for a completed Stripe Checkout in one transaction:
 * insert the credit_purchases audit row, the committed credit_ledger
 * row, and cross-link them. Idempotent via `uniq_credit_ledger_kind_ref`
 * on `(kind='stripe_purchase', reference_id=stripe_checkout_id)` —
 * duplicate webhook deliveries throw a unique-violation which the
 * caller should treat as "already processed, return 200."
 */
export async function grantStripePurchase(params: {
  userId: string;
  stripeCheckoutId: string;
  stripePaymentIntentId: string | null;
  amountCents: number;
  currency: string;
  credits: number;
  rawWebhook: Stripe.Event;
}): Promise<{ ledgerEntryId: string; purchaseId: string }> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    const ledgerRes = await client.query<{ id: string }>(
      `INSERT INTO credit_ledger
           (user_id, delta, state, kind, reference_id, reason)
         VALUES ($1, $2, 'committed', 'stripe_purchase', $3, NULL)
         RETURNING id`,
      [params.userId, params.credits, params.stripeCheckoutId]
    );
    const ledgerEntry = ledgerRes.rows[0];
    if (!ledgerEntry) throw new Error("ledger insert returned no row");

    const purchaseRes = await client.query<{ id: string }>(
      `INSERT INTO credit_purchases
           (user_id, stripe_checkout_id, stripe_payment_intent_id,
            amount_cents, currency, credits_granted, ledger_entry_id,
            status, raw_webhook)
         VALUES ($1, $2, $3, $4, $5, $6, $7, 'completed', $8)
         RETURNING id`,
      [
        params.userId,
        params.stripeCheckoutId,
        params.stripePaymentIntentId,
        params.amountCents,
        params.currency,
        params.credits,
        ledgerEntry.id,
        JSON.stringify(params.rawWebhook),
      ]
    );
    const purchaseEntry = purchaseRes.rows[0];
    if (!purchaseEntry) throw new Error("purchase insert returned no row");

    await client.query("COMMIT");
    return { ledgerEntryId: ledgerEntry.id, purchaseId: purchaseEntry.id };
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
}

/**
 * Admin comp — grant credits directly, bypassing Stripe. Inserts a
 * ledger row with kind='admin_credit'; the reason and the granting
 * admin are persisted for audit.
 *
 * No reference_id → admin grants don't compete with the idempotency
 * index. Admins can grant the same user multiple times.
 */
export async function grantAdminCredit(params: {
  userId: string;
  adminId: string;
  credits: number;
  reason: string;
}): Promise<string> {
  if (params.credits <= 0) {
    throw new Error("admin credit amount must be positive");
  }
  const row = await queryOne<{ id: string }>(
    `INSERT INTO credit_ledger
         (user_id, delta, state, kind, reason, created_by_admin_id)
       VALUES ($1, $2, 'committed', 'admin_credit', $3, $4)
       RETURNING id`,
    [params.userId, params.credits, params.reason, params.adminId]
  );
  if (!row) throw new Error("insert returned no id");
  return row.id;
}

/**
 * Place a hold — a negative-delta ledger row in state 'held'. Used by
 * phase-1b's report queue to debit the spendable balance at submit
 * time, before any LLM work has happened. If the report succeeds, the
 * hold flips to 'committed'. If it fails, the hold flips to 'refunded'
 * (credits drop back to spendable).
 *
 * Idempotent per (kind='report_hold', reference_id=reportJobId): a
 * duplicate call throws a unique-violation, which the caller should
 * treat as "hold already placed, proceed to the commit/release path."
 */
export async function holdCredits(params: {
  userId: string;
  amount: number;
  reportJobId: string;
}): Promise<string> {
  if (params.amount <= 0) {
    throw new Error("hold amount must be positive");
  }
  const row = await queryOne<{ id: string }>(
    `INSERT INTO credit_ledger
         (user_id, delta, state, kind, reference_id)
       VALUES ($1, $2, 'held', 'report_hold', $3)
       RETURNING id`,
    [params.userId, -params.amount, params.reportJobId]
  );
  if (!row) throw new Error("insert returned no id");
  return row.id;
}

/**
 * Finalise a hold as a real debit. Idempotent: a hold already
 * committed or refunded is a no-op.
 */
export async function commitHold(holdLedgerId: string): Promise<void> {
  await query(
    `UPDATE credit_ledger
        SET state = 'committed'
      WHERE id = $1
        AND state = 'held'
        AND kind = 'report_hold'`,
    [holdLedgerId]
  );
}

/**
 * Release a hold — credits refund to spendable balance. The reason
 * is stored for audit and surfaced in the user's ledger history.
 * Idempotent: a hold already committed or refunded is a no-op.
 */
export async function releaseHold(
  holdLedgerId: string,
  reason: string
): Promise<void> {
  await query(
    `UPDATE credit_ledger
        SET state = 'refunded',
            reason = $2
      WHERE id = $1
        AND state = 'held'
        AND kind = 'report_hold'`,
    [holdLedgerId, reason]
  );
}
