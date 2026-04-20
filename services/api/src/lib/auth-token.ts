import { SignJWT, jwtVerify, type JWTPayload } from "jose";
import { config } from "../config.js";

/**
 * Issue + verify end-user session tokens.
 *
 * This module is the designated IdP-swap seam. Phase 1 mints and
 * validates HS256 JWTs signed with `JWT_SECRET`. A future Phase 2 swap
 * to Keycloak / Zitadel / Logto replaces these implementations with a
 * JWKS verifier (jose.createRemoteJWKSet + jwtVerify). Every route that
 * calls `verifyToken` stays untouched — the seam is deliberate.
 *
 * Tokens live in an httpOnly cookie (`sw_session`). Their payload is
 * intentionally small: we carry user id + email only. Anything larger
 * (roles, display_name, preferences) should be fetched from the DB on
 * demand — JWT claims don't get invalidated when the underlying row
 * changes.
 */

// 30 days. Long-lived is fine because phase 1 has no
// force-logout-all-devices requirement; rotating JWT_SECRET is the
// phase-1 "revoke everyone" button.
const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60;

const ISSUER = "canadianpoliticaldata";
const AUDIENCE = "cpd-users";

export interface SessionClaims {
  sub: string;   // user id (UUID)
  email: string;
}

let cachedKey: Uint8Array | null = null;
function getKey(): Uint8Array {
  if (!config.jwtSecret) {
    throw new Error("JWT_SECRET not configured");
  }
  if (!cachedKey) {
    cachedKey = new TextEncoder().encode(config.jwtSecret);
  }
  return cachedKey;
}

export function isConfigured(): boolean {
  return Boolean(config.jwtSecret);
}

export async function signSessionToken(claims: SessionClaims): Promise<string> {
  return new SignJWT({ email: claims.email } satisfies Omit<SessionClaims, "sub"> as JWTPayload)
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setSubject(claims.sub)
    .setIssuedAt()
    .setIssuer(ISSUER)
    .setAudience(AUDIENCE)
    .setExpirationTime(`${SESSION_TTL_SECONDS}s`)
    .sign(getKey());
}

export async function verifySessionToken(token: string): Promise<SessionClaims | null> {
  try {
    const { payload } = await jwtVerify(token, getKey(), {
      issuer: ISSUER,
      audience: AUDIENCE,
    });
    if (typeof payload.sub !== "string" || typeof payload.email !== "string") {
      return null;
    }
    return { sub: payload.sub, email: payload.email };
  } catch {
    return null;
  }
}

export const SESSION_COOKIE = "sw_session";
export const SESSION_TTL_S = SESSION_TTL_SECONDS;
