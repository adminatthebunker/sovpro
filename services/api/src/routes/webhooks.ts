import type { FastifyInstance } from "fastify";
import { createHmac, timingSafeEqual } from "node:crypto";
import { config } from "../config.js";
import { query } from "../db.js";

/**
 * Webhook receiver for the `change` detection service.
 * Expected headers: X-Signature: sha256=<hex(hmac(secret, rawBody))>
 * Body shape (we're flexible — just record what we get):
 *   { url: string, diff?: string, at?: string, type?: string, meta?: object }
 */
export default async function webhookRoutes(app: FastifyInstance) {
  const rawBodyBuckets = new WeakMap<object, Buffer>();

  app.addHook("preParsing", async (req, _reply, payload) => {
    if (req.routeOptions?.url?.startsWith("/api/v1/webhooks")) {
      const chunks: Buffer[] = [];
      for await (const chunk of payload) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as string));
      }
      const raw = Buffer.concat(chunks);
      rawBodyBuckets.set(req as unknown as object, raw);
      // Re-emit a stream for Fastify's JSON parser
      const { Readable } = await import("node:stream");
      return Readable.from([raw]);
    }
    return payload;
  });

  app.post("/change", async (req, reply) => {
    const raw = rawBodyBuckets.get(req as unknown as object) ?? Buffer.from(JSON.stringify(req.body ?? {}));
    if (!verifySignature(raw, req.headers["x-signature"] as string | undefined)) {
      return reply.unauthorized("invalid signature");
    }

    const body = req.body as {
      url?: string; type?: string; diff?: string; at?: string;
      old?: string; new?: string; meta?: Record<string, unknown>;
    } | undefined;

    if (!body?.url) return reply.badRequest("url required");

    // Resolve URL → website_id
    const rows = await query<{ id: string }>(
      `SELECT id FROM websites WHERE url = $1 OR hostname = lower($2) LIMIT 1`,
      [body.url, body.url.replace(/^https?:\/\//, "").replace(/\/.*$/, "")]
    );
    if (!rows.length) {
      app.log.warn({ url: body.url }, "webhook for unknown website — recorded as orphan");
    }

    const websiteId = rows[0]?.id ?? null;
    if (!websiteId) {
      return { ok: true, recorded: false, reason: "unknown website" };
    }

    await query(
      `INSERT INTO scan_changes (website_id, change_type, old_value, new_value, severity, summary, details)
       VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)`,
      [
        websiteId,
        body.type ?? "content_changed",
        body.old ?? null,
        body.new ?? null,
        "info",
        body.type ? `External ${body.type} detected` : "Content change detected",
        JSON.stringify({ diff: body.diff, meta: body.meta }),
      ]
    );

    return { ok: true, recorded: true, website_id: websiteId };
  });
}

function verifySignature(body: Buffer, header: string | undefined): boolean {
  if (!config.webhookSecret) return true; // no secret configured — accept (dev mode)
  if (!header) return false;
  const provided = header.replace(/^sha256=/, "");
  const expected = createHmac("sha256", config.webhookSecret).update(body).digest("hex");
  if (provided.length !== expected.length) return false;
  try {
    return timingSafeEqual(Buffer.from(provided, "hex"), Buffer.from(expected, "hex"));
  } catch {
    return false;
  }
}
