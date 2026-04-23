import type { FastifyReply, FastifyRequest } from "fastify";
import { queryOne } from "../db.js";
import {
  SESSION_COOKIE,
  isConfigured as jwtIsConfigured,
  verifySessionToken,
  type SessionClaims,
} from "../lib/auth-token.js";

/**
 * End-user session middleware. Mirrors services/api/src/middleware/admin-auth.ts
 * in shape:
 *  - 503 when the feature is disabled by missing config (JWT_SECRET unset).
 *  - 401 for missing / invalid session.
 *  - Attaches a small marker to the request for downstream handlers.
 *
 * Two preHandlers:
 *  - `requireUser`: 401s if no valid session. Use on /me/* and anywhere
 *    that demands an identified caller.
 *  - `optionalUser`: attaches req.user when a valid session is present;
 *    otherwise leaves it unset and allows the request through. Use on
 *    public endpoints that want to personalize (e.g. future "show my
 *    saved searches matching this query" on /search).
 *
 * The session cookie is httpOnly + Secure + SameSite=Lax. Route code
 * that mutates state additionally checks the double-submit CSRF cookie
 * (see routes/me.ts).
 */

export interface AuthedRequest extends FastifyRequest {
  user?: SessionClaims;
  /** Set by requireAdmin after a successful DB lookup. */
  adminEmail?: string;
}

function readSessionCookie(req: FastifyRequest): string | null {
  // @fastify/cookie populates req.cookies
  const cookies = (req as FastifyRequest & { cookies?: Record<string, string | undefined> }).cookies;
  return cookies?.[SESSION_COOKIE] ?? null;
}

export async function requireUser(req: FastifyRequest, reply: FastifyReply) {
  if (!jwtIsConfigured()) {
    return reply.code(503).send({
      error: "user accounts disabled: JWT_SECRET not configured on server",
    });
  }
  const token = readSessionCookie(req);
  if (!token) {
    return reply.code(401).send({ error: "not signed in" });
  }
  const claims = await verifySessionToken(token);
  if (!claims) {
    return reply.code(401).send({ error: "invalid or expired session" });
  }

  // Per-request suspension check. An admin flipping a user's
  // rate_limit_tier to 'suspended' takes effect on the next request,
  // matching the same "re-read every request" discipline requireAdmin
  // uses for is_admin. One extra query on every authenticated /me/*
  // call is cheap at the traffic levels this project runs at; when
  // that changes, cache with a 30-second TTL.
  const tierRow = await queryOne<{ rate_limit_tier: string }>(
    `SELECT rate_limit_tier FROM users WHERE id = $1`,
    [claims.sub]
  );
  if (tierRow?.rate_limit_tier === "suspended") {
    return reply.code(403).send({ error: "account suspended" });
  }

  (req as AuthedRequest).user = claims;
}

export async function optionalUser(req: FastifyRequest, _reply: FastifyReply) {
  if (!jwtIsConfigured()) return;
  const token = readSessionCookie(req);
  if (!token) return;
  const claims = await verifySessionToken(token);
  if (claims) {
    (req as AuthedRequest).user = claims;
  }
}

export function getUser(req: FastifyRequest): SessionClaims | null {
  return (req as AuthedRequest).user ?? null;
}

/**
 * Admin gate. Runs requireUser first, then checks users.is_admin on
 * every request. We re-read from the DB rather than embedding the
 * flag in the session JWT so a psql demotion (UPDATE users SET
 * is_admin = false) takes effect on the next request, not on next
 * session expiry. Admin traffic volume makes this cheap.
 *
 * 403 on authenticated-but-not-admin is intentional: 401 would imply
 * "try again with credentials," but the user is already signed in —
 * they just don't have the role. Returning 404 is also defensible
 * (hide the admin surface entirely) but would make legitimate "I
 * should have access, what broke?" debugging harder, and the admin
 * panel is already at a known URL.
 */
export async function requireAdmin(req: FastifyRequest, reply: FastifyReply) {
  await requireUser(req, reply);
  if (reply.sent) return;

  const claims = (req as AuthedRequest).user;
  if (!claims) return; // requireUser already replied

  const row = await queryOne<{ is_admin: boolean; email: string }>(
    `SELECT is_admin, email FROM users WHERE id = $1`,
    [claims.sub]
  );
  if (!row || !row.is_admin) {
    return reply.code(403).send({ error: "admin access required" });
  }
  (req as AuthedRequest).adminEmail = row.email;
}

export function getAdminEmail(req: FastifyRequest): string | null {
  return (req as AuthedRequest).adminEmail ?? null;
}
