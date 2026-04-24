import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { createHash, randomBytes } from "node:crypto";
import { pool, query, queryOne } from "../db.js";
import { config } from "../config.js";
import {
  SESSION_COOKIE,
  SESSION_TTL_S,
  isConfigured as jwtIsConfigured,
  signSessionToken,
} from "../lib/auth-token.js";
import { sendMagicLink, emailIsConfigured } from "../lib/email.js";
import { checkDeliverableDomain } from "../lib/email-domain.js";
import { generateCsrfToken, setCsrfCookie } from "../lib/csrf.js";

/**
 * Magic-link auth routes.
 *
 * POST /auth/request-link  { email }  → 202 always (no enumeration signal)
 * POST /auth/verify        { token }  → 200 with session cookie set
 *
 * Both endpoints rate-limit aggressively. request-link is the expensive
 * one (it sends email) and the tempting attack surface (spam via our
 * SMTP, enumeration). verify is cheap; the rate limit there is just to
 * cap brute-forcing of the 256-bit nonce, which is already
 * astronomically infeasible.
 *
 * If JWT_SECRET is unset both endpoints return 503 (feature disabled).
 */

const NONCE_BYTES = 32;                      // 256 bits
const LINK_TTL_MINUTES = 15;

function hashNonce(nonce: string): Buffer {
  return createHash("sha256").update(nonce, "utf8").digest();
}

const requestLinkBody = z.object({
  email: z.string().trim().email().max(320).toLowerCase(),
});

const verifyBody = z.object({
  token: z.string().min(32).max(128),
});

interface UserRow {
  id: string;
  email: string;
  display_name: string | null;
}

interface TokenRow {
  id: string;
  email: string;
  expires_at: string;
  consumed_at: string | null;
}

export default async function authRoutes(app: FastifyInstance) {
  // ── POST /auth/request-link ──────────────────────────────────
  app.post(
    "/request-link",
    {
      config: {
        rateLimit: {
          // Aggressive: 5/hr/IP + 3/hr/email. We bucket by IP here; the
          // per-email cap is enforced in the handler via a DB count so
          // it survives rotating IPs.
          max: 5,
          timeWindow: "1 hour",
        },
      },
    },
    async (req, reply) => {
      if (!jwtIsConfigured()) {
        return reply.code(503).send({
          error: "user accounts disabled: JWT_SECRET not configured on server",
        });
      }

      const parsed = requestLinkBody.safeParse(req.body);
      if (!parsed.success) {
        return reply.code(400).send({ error: "invalid email" });
      }
      const { email } = parsed.data;

      // RFC 2606 reserved domains (example.com, *.test, *.invalid,
      // *.localhost) can never receive mail. Reject at the edge so
      // test signups don't leak into prod and we don't burn SMTP
      // quota / reputation on guaranteed-bouncing addresses. Safe to
      // 400 (not 202) since these domains cannot legitimately host
      // users, so the response leaks no enumeration signal.
      const domainCheck = checkDeliverableDomain(email);
      if (!domainCheck.ok) {
        req.log.info(
          { email, reason: domainCheck.reason },
          "[auth] request-link: rejected reserved domain"
        );
        return reply.code(400).send({
          error: "that email domain cannot receive mail — please use a real address",
        });
      }

      // Per-email rate-limit: at most 3 unconsumed tokens in the last
      // hour. This runs post-IP-limit so a distributed attacker can't
      // trivially spam a single mailbox by rotating source IPs.
      const recent = await queryOne<{ n: string }>(
        `SELECT count(*)::text AS n
           FROM login_tokens
          WHERE email = $1
            AND created_at > now() - interval '1 hour'`,
        [email]
      );
      if (recent && Number(recent.n) >= 3) {
        // Still 202 to preserve the no-enumeration property — we just
        // don't send another email. The user sees the original one.
        req.log.info({ email }, "[auth] request-link: per-email limit hit");
        return reply.code(202).send({ ok: true });
      }

      // Generate nonce, hash, store.
      const nonce = randomBytes(NONCE_BYTES).toString("hex");
      const tokenHash = hashNonce(nonce);
      const requestedIp = req.ip ?? null;

      await query(
        `INSERT INTO login_tokens (email, token_hash, expires_at, requested_ip)
         VALUES ($1, $2, now() + ($3 || ' minutes')::interval, $4)`,
        [email, tokenHash as unknown as string, String(LINK_TTL_MINUTES), requestedIp as unknown as string]
      );

      const verifyUrl = `${config.publicSiteUrl}/auth/verify?token=${encodeURIComponent(nonce)}`;
      try {
        await sendMagicLink({ to: email, url: verifyUrl }, req.log);
      } catch (err) {
        req.log.error({ err, email }, "[auth] failed to send magic link");
        // Still 202 so the client gets consistent behaviour and we don't
        // leak upstream failure details. Operator sees the error in logs.
      }

      if (!emailIsConfigured()) {
        req.log.info(
          { email, verifyUrl },
          "[auth] SMTP not configured; magic link logged above — copy it to complete sign-in"
        );
      }

      return reply.code(202).send({ ok: true });
    }
  );

  // ── POST /auth/verify ────────────────────────────────────────
  app.post(
    "/verify",
    {
      config: {
        rateLimit: {
          max: 10,
          timeWindow: "1 hour",
        },
      },
    },
    async (req, reply) => {
      if (!jwtIsConfigured()) {
        return reply.code(503).send({
          error: "user accounts disabled: JWT_SECRET not configured on server",
        });
      }

      const parsed = verifyBody.safeParse(req.body);
      if (!parsed.success) {
        return reply.code(400).send({ error: "invalid token" });
      }
      const { token } = parsed.data;
      const tokenHash = hashNonce(token);

      // Single transaction: redeem the token and upsert the user.
      const client = await pool.connect();
      try {
        await client.query("BEGIN");

        // Timing-safe compare is enforced by the BYTEA UNIQUE index +
        // exact-match query; Postgres doesn't have the side-channel a
        // manual hash compare would. Still guard with consumed_at.
        const tokRows = await client.query<TokenRow>(
          `SELECT id, email, expires_at, consumed_at
             FROM login_tokens
            WHERE token_hash = $1
            FOR UPDATE`,
          [tokenHash]
        );
        const tok = tokRows.rows[0];
        if (!tok) {
          await client.query("ROLLBACK");
          return reply.code(400).send({ error: "invalid or expired token" });
        }
        if (tok.consumed_at) {
          await client.query("ROLLBACK");
          return reply.code(400).send({ error: "token already used" });
        }
        if (new Date(tok.expires_at).getTime() < Date.now()) {
          await client.query("ROLLBACK");
          return reply.code(400).send({ error: "token expired" });
        }

        // Mark consumed. Deliberately not deleting — the row stays
        // briefly for audit. A periodic cleanup job can purge old
        // consumed/expired rows if we ever care.
        await client.query(
          `UPDATE login_tokens SET consumed_at = now() WHERE id = $1`,
          [tok.id]
        );

        // Upsert user on the email identity key. last_login_at is what
        // the UI surfaces as "last sign in". We also clear
        // email_bounced_at — a successful magic-link redemption is
        // direct evidence the inbox is alive again (the link wouldn't
        // have reached the user otherwise), so the alerts-worker can
        // resume sending for this user from the next tick.
        const userRows = await client.query<UserRow>(
          `INSERT INTO users (email, last_login_at)
           VALUES ($1, now())
           ON CONFLICT (email) DO UPDATE
             SET last_login_at = EXCLUDED.last_login_at,
                 email_bounced_at = NULL
           RETURNING id, email, display_name`,
          [tok.email]
        );
        const user = userRows.rows[0];
        if (!user) {
          await client.query("ROLLBACK");
          req.log.error({ email: tok.email }, "[auth] upsert returned no row");
          return reply.code(500).send({ error: "verify failed" });
        }

        await client.query("COMMIT");

        const sessionJwt = await signSessionToken({ sub: user.id, email: user.email });
        const csrfToken = generateCsrfToken();

        reply.setCookie(SESSION_COOKIE, sessionJwt, {
          httpOnly: true,
          secure: true,
          sameSite: "lax",
          path: "/",
          maxAge: SESSION_TTL_S,
        });
        setCsrfCookie(reply, csrfToken, SESSION_TTL_S);

        return reply.code(200).send({
          id: user.id,
          email: user.email,
          display_name: user.display_name,
        });
      } catch (err) {
        await client.query("ROLLBACK").catch(() => {});
        req.log.error({ err }, "[auth] verify transaction failed");
        return reply.code(500).send({ error: "verify failed" });
      } finally {
        client.release();
      }
    }
  );
}

// Exported for tests / diagnostics.
export const _internal = { hashNonce, NONCE_BYTES, LINK_TTL_MINUTES };
