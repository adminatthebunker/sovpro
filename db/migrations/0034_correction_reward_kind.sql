-- Correction rewards: extend credit_ledger.kind with 'correction_reward'.
--
-- When an admin transitions a non-anonymous correction_submissions
-- row into status='applied', the submitter gets a fixed credit grant
-- (amount = CORRECTION_REWARD_CREDITS, default 10). That grant is a
-- credit_ledger row with kind='correction_reward' and
-- reference_id=correction_submissions.id.
--
-- The existing uniq_credit_ledger_kind_ref partial unique index
-- (defined in 0033) already covers (kind, reference_id) where
-- reference_id IS NOT NULL, so applying the same correction twice is
-- automatically idempotent — the second INSERT raises unique_violation
-- (SQLSTATE 23505) and the caller swallows it.
--
-- This migration only needs to ALTER the existing CHECK constraint on
-- credit_ledger.kind to add the new literal. Postgres requires drop +
-- add for CHECK constraints (no ALTER ADD CHECK on existing column).
-- The constraint name is the default assigned by 0033's inline check;
-- we look it up dynamically so the migration survives any prior
-- manual-rename operations.

DO $$
DECLARE
  cname text;
BEGIN
  SELECT conname INTO cname
    FROM pg_constraint
   WHERE conrelid = 'credit_ledger'::regclass
     AND contype  = 'c'
     AND pg_get_constraintdef(oid) LIKE '%stripe_purchase%'
     AND pg_get_constraintdef(oid) LIKE '%admin_credit%'
   LIMIT 1;
  IF cname IS NULL THEN
    RAISE EXCEPTION 'expected kind CHECK constraint on credit_ledger not found';
  END IF;
  EXECUTE format('ALTER TABLE credit_ledger DROP CONSTRAINT %I', cname);
END $$;

ALTER TABLE credit_ledger
  ADD CONSTRAINT credit_ledger_kind_check
  CHECK (kind IN (
    'stripe_purchase',
    'admin_credit',
    'report_hold',
    'report_commit',
    'report_refund',
    'correction_reward'
  ));
