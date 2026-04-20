import type { FastifyReply, FastifyRequest } from "fastify";
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
