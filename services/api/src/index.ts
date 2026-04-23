import Fastify from "fastify";
import cookie from "@fastify/cookie";
import cors from "@fastify/cors";
import helmet from "@fastify/helmet";
import rateLimit from "@fastify/rate-limit";
import sensible from "@fastify/sensible";
import { config } from "./config.js";
import { pool, shutdown } from "./db.js";
import politicianRoutes from "./routes/politicians.js";
import organizationRoutes from "./routes/organizations.js";
import mapRoutes from "./routes/map.js";
import statsRoutes from "./routes/stats.js";
import changesRoutes from "./routes/changes.js";
import lookupRoutes from "./routes/lookup.js";
import partyRoutes from "./routes/parties.js";
import albertaRoutes from "./routes/alberta.js";
import webhookRoutes from "./routes/webhooks.js";
import ogRoutes from "./routes/og.js";
import socialsRoutes from "./routes/socials.js";
import committeesRoutes from "./routes/committees.js";
import openparliamentRoutes from "./routes/openparliament.js";
import coverageRoutes from "./routes/coverage.js";
import searchRoutes from "./routes/search.js";
import speechRoutes from "./routes/speeches.js";
import adminRoutes from "./routes/admin.js";
import authRoutes from "./routes/auth.js";
import meRoutes from "./routes/me.js";
import alertRoutes from "./routes/alerts.js";
import feedRoutes from "./routes/feeds.js";
import correctionsRoutes, { meCorrectionsRoutes } from "./routes/corrections.js";
import contradictionsRoutes from "./routes/contradictions.js";
import creditsRoutes from "./routes/credits.js";
import stripeWebhookRoutes from "./routes/stripe-webhook.js";
import rateLimitRequestRoutes from "./routes/rate-limit-requests.js";

const app = Fastify({
  logger: {
    level: config.logLevel,
    transport: config.env === "development"
      ? { target: "pino-pretty", options: { translateTime: "SYS:HH:MM:ss", ignore: "pid,hostname" } }
      : undefined,
  },
  trustProxy: true,
  bodyLimit: 1_000_000,
});

await app.register(helmet, {
  contentSecurityPolicy: false,
  crossOriginResourcePolicy: { policy: "cross-origin" },
});
await app.register(cors, { origin: config.corsOrigin, credentials: true });
await app.register(cookie);
await app.register(sensible);
await app.register(rateLimit, {
  max: 300,
  timeWindow: "1 minute",
});

// ── Health ───────────────────────────────────────────────────
app.get("/health", async () => {
  try {
    const r = await pool.query("SELECT 1 AS ok");
    return { ok: true, db: r.rows[0].ok === 1 };
  } catch (e) {
    app.log.error(e, "health check db failed");
    return { ok: false, db: false };
  }
});

app.get("/", async () => ({
  service: "canadianpoliticaldata-api",
  version: "0.1.0",
  docs: "/api/v1",
}));

// ── Routes ───────────────────────────────────────────────────
await app.register(politicianRoutes, { prefix: "/api/v1/politicians" });
await app.register(organizationRoutes, { prefix: "/api/v1/organizations" });
await app.register(mapRoutes, { prefix: "/api/v1/map" });
await app.register(statsRoutes, { prefix: "/api/v1/stats" });
await app.register(changesRoutes, { prefix: "/api/v1/changes" });
await app.register(lookupRoutes, { prefix: "/api/v1/lookup" });
await app.register(partyRoutes, { prefix: "/api/v1/parties" });
await app.register(albertaRoutes, { prefix: "/api/v1/alberta" });
await app.register(webhookRoutes, { prefix: "/api/v1/webhooks" });
await app.register(ogRoutes, { prefix: "/api/v1/og" });
await app.register(socialsRoutes, { prefix: "/api/v1/socials" });
await app.register(committeesRoutes, { prefix: "/api/v1/committees" });
await app.register(coverageRoutes, { prefix: "/api/v1/coverage" });
await app.register(searchRoutes, { prefix: "/api/v1/search" });
await app.register(speechRoutes, { prefix: "/api/v1/speeches" });
await app.register(adminRoutes, { prefix: "/api/v1/admin" });
await app.register(authRoutes, { prefix: "/api/v1/auth" });
await app.register(meRoutes, { prefix: "/api/v1/me" });
await app.register(alertRoutes, { prefix: "/api/v1/alerts" });
await app.register(feedRoutes, { prefix: "/api/v1/feeds" });
await app.register(correctionsRoutes, { prefix: "/api/v1/corrections" });
await app.register(meCorrectionsRoutes, { prefix: "/api/v1/me" });
await app.register(contradictionsRoutes, { prefix: "/api/v1/contradictions" });
// Credits (billing rail). User-facing balance + checkout under /me/credits;
// Stripe webhook handler is a separate plugin with a plugin-scoped raw-body
// parser so signature verification isn't broken by Fastify's default JSON
// body parsing.
await app.register(creditsRoutes, { prefix: "/api/v1/me/credits" });
await app.register(rateLimitRequestRoutes, { prefix: "/api/v1/me/rate-limit-requests" });
await app.register(stripeWebhookRoutes, { prefix: "/api/v1/webhooks/stripe" });
// Mounted under the same /politicians prefix so the final URL is
// /api/v1/politicians/:id/openparliament (REST sub-resource pattern).
await app.register(openparliamentRoutes, { prefix: "/api/v1/politicians" });

const stop = async (signal: string) => {
  app.log.info({ signal }, "shutting down");
  try {
    await app.close();
    await shutdown();
    process.exit(0);
  } catch (err) {
    app.log.error(err, "forced exit");
    process.exit(1);
  }
};

process.on("SIGINT", () => stop("SIGINT"));
process.on("SIGTERM", () => stop("SIGTERM"));

try {
  await app.listen({ host: config.host, port: config.port });
  app.log.info(`API listening on ${config.host}:${config.port}`);
} catch (err) {
  app.log.error(err, "failed to listen");
  process.exit(1);
}
