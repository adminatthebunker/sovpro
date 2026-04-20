import { randomBytes, timingSafeEqual } from "node:crypto";
import type { FastifyReply, FastifyRequest } from "fastify";

/**
 * Double-submit CSRF cookie.
 *
 * Standard pattern: issue a non-httpOnly cookie with a random value at
 * login. The frontend reads it (same-origin JS has no CORS barrier) and
 * echoes it on every mutating request via the X-CSRF-Token header.
 * Server compares header against cookie in constant time.
 *
 * Why this works: a CSRF attacker can make the victim's browser send
 * the cookie, but cannot read it to set the header (httpOnly doesn't
 * matter here — it's the SameSite policy plus the browser's
 * cross-origin read isolation that protects the token value). Any
 * request missing the header, or with a mismatched header, is rejected.
 *
 * SameSite=Lax on the session cookie already blocks most CSRF; the
 * double-submit is belt-and-suspenders and required for any future
 * SameSite=None scenarios (embedded widgets, etc.).
 */

export const CSRF_COOKIE = "sw_csrf";
export const CSRF_HEADER = "x-csrf-token";

export function generateCsrfToken(): string {
  return randomBytes(32).toString("hex");
}

export function setCsrfCookie(reply: FastifyReply, token: string, maxAgeSeconds: number): void {
  reply.setCookie(CSRF_COOKIE, token, {
    httpOnly: false,          // frontend must read this to echo it
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: maxAgeSeconds,
  });
}

export function clearCsrfCookie(reply: FastifyReply): void {
  reply.clearCookie(CSRF_COOKIE, { path: "/" });
}

/** Verify the double-submit on a mutating request. Returns true on match. */
export function verifyCsrf(req: FastifyRequest): boolean {
  const cookies = (req as FastifyRequest & { cookies?: Record<string, string | undefined> }).cookies;
  const cookieToken = cookies?.[CSRF_COOKIE];
  const headerToken = req.headers[CSRF_HEADER];
  if (!cookieToken || typeof headerToken !== "string" || !headerToken) return false;
  const a = Buffer.from(cookieToken, "utf8");
  const b = Buffer.from(headerToken, "utf8");
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

/** Preferred preHandler wrapper for mutating /me/* routes. */
export async function requireCsrf(req: FastifyRequest, reply: FastifyReply) {
  if (!verifyCsrf(req)) {
    return reply.code(403).send({ error: "csrf check failed" });
  }
}
