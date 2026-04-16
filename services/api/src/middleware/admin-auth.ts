import { timingSafeEqual } from "node:crypto";
import type { FastifyReply, FastifyRequest } from "fastify";
import { config } from "../config.js";

/**
 * Fastify preHandler that gates a route on a shared ADMIN_TOKEN bearer.
 *
 * Design notes:
 *
 * - We use `timingSafeEqual` to avoid classic side-channel token leaks.
 *   The two buffers must be equal length, so we pad the candidate to
 *   the stored-token's byte length before comparing (the padding is
 *   guaranteed not to match, so this doesn't weaken the check).
 *
 * - If `config.adminToken` is the empty string (ADMIN_TOKEN unset),
 *   every admin call returns 503 rather than 401. This makes it
 *   obvious the panel is disabled by config, not that the user got
 *   the token wrong. A startup warning is already emitted from
 *   config.ts in production mode.
 *
 * - The token is NEVER echoed into logs (Fastify's default logger
 *   doesn't log request bodies; Authorization is a header and Helmet
 *   doesn't touch logs). Relying on that here rather than adding a
 *   redact rule — if we ever enable request-body logging we need to
 *   add a redact path for `headers.authorization`.
 */
export async function requireAdminToken(
  req: FastifyRequest,
  reply: FastifyReply
) {
  if (!config.adminToken) {
    return reply.code(503).send({
      error: "admin panel disabled: ADMIN_TOKEN not configured on server",
    });
  }

  const header = req.headers.authorization ?? "";
  if (!header.toLowerCase().startsWith("bearer ")) {
    return reply.code(401).send({ error: "missing bearer token" });
  }
  const candidate = header.slice(7).trim();
  if (!candidate) {
    return reply.code(401).send({ error: "missing bearer token" });
  }

  const a = Buffer.from(config.adminToken, "utf8");
  // Pad the candidate to match length; resulting mismatch is what we want.
  const bRaw = Buffer.from(candidate, "utf8");
  const b = Buffer.alloc(a.length);
  bRaw.copy(b, 0, 0, Math.min(a.length, bRaw.length));

  if (bRaw.length !== a.length || !timingSafeEqual(a, b)) {
    return reply.code(401).send({ error: "invalid admin token" });
  }
  // Attach a small marker for downstream handlers that want to note
  // "who" requested something. Single-admin for now.
  (req as FastifyRequest & { adminActor?: string }).adminActor = "admin";
}
