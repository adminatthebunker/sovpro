import Fastify from "fastify";
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
import webhookRoutes from "./routes/webhooks.js";
import ogRoutes from "./routes/og.js";

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
await app.register(cors, { origin: config.corsOrigin });
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
  service: "sovereignwatch-api",
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
await app.register(webhookRoutes, { prefix: "/api/v1/webhooks" });
await app.register(ogRoutes, { prefix: "/api/v1/og" });

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
