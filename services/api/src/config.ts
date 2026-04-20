import { z } from "zod";

const schema = z.object({
  NODE_ENV: z.enum(["development", "production", "test"]).default("production"),
  API_PORT: z.coerce.number().int().default(3000),
  API_HOST: z.string().default("0.0.0.0"),
  API_LOG_LEVEL: z.enum(["trace","debug","info","warn","error","fatal"]).default("info"),
  API_CORS_ORIGIN: z.string().default("*"),
  DATABASE_URL: z.string().url().or(z.string().startsWith("postgres")),
  CHANGE_WEBHOOK_SECRET: z.string().min(16).optional(),
  WEBHOOK_SECRET: z.string().min(16).optional(),
  // Shared bearer token for the /admin panel. 32+ chars of url-safe
  // entropy is a reasonable floor; missing in dev is OK (admin routes
  // will simply reject all callers with 503 until set), but NODE_ENV
  // === "production" without ADMIN_TOKEN is a boot-time warning.
  ADMIN_TOKEN: z.string().min(32).optional(),
  TEI_URL: z.string().default("http://tei:80"),
  // End-user auth (phase 1 magic-link). Unset → /api/v1/auth/* and
  // /api/v1/me/* respond 503 (feature disabled), same ergonomics as
  // ADMIN_TOKEN.
  JWT_SECRET: z.string().min(32).optional(),
  // SMTP (Proton submission in prod). Unset → email.ts runs in
  // dev-stub mode and logs would-be links to server logs.
  SMTP_HOST: z.string().default("smtp.protonmail.ch"),
  SMTP_PORT: z.coerce.number().int().default(587),
  SMTP_USERNAME: z.string().optional(),
  SMTP_PASSWORD: z.string().optional(),
  SMTP_FROM: z.string().optional(),
  // Used when building magic-link URLs in outgoing emails.
  PUBLIC_SITE_URL: z.string().url().default("http://localhost:5173"),
});

export const config = (() => {
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    console.error("Invalid environment:", parsed.error.format());
    process.exit(1);
  }
  const env = parsed.data;
  return {
    env: env.NODE_ENV,
    port: env.API_PORT,
    host: env.API_HOST,
    logLevel: env.API_LOG_LEVEL,
    corsOrigin: env.API_CORS_ORIGIN,
    databaseUrl: env.DATABASE_URL,
    webhookSecret: env.CHANGE_WEBHOOK_SECRET ?? env.WEBHOOK_SECRET ?? "",
    adminToken: env.ADMIN_TOKEN ?? "",
    teiUrl: env.TEI_URL.replace(/\/$/, ""),
    jwtSecret: env.JWT_SECRET ?? "",
    smtp: {
      host: env.SMTP_HOST,
      port: env.SMTP_PORT,
      username: env.SMTP_USERNAME ?? "",
      password: env.SMTP_PASSWORD ?? "",
      from: env.SMTP_FROM ?? "",
    },
    publicSiteUrl: env.PUBLIC_SITE_URL.replace(/\/$/, ""),
  };
})();

if (config.env === "production" && !config.adminToken) {
  console.warn(
    "[config] ADMIN_TOKEN is unset in production; /api/v1/admin/* routes will reject all callers."
  );
}

if (config.env === "production" && !config.jwtSecret) {
  console.warn(
    "[config] JWT_SECRET is unset in production; /api/v1/auth/* + /api/v1/me/* will reject all callers."
  );
}

if (config.env === "production" && (!config.smtp.password || !config.smtp.username)) {
  console.warn(
    "[config] SMTP credentials unset in production; magic-link emails will be logged to stdout instead of sent."
  );
}

export type Config = typeof config;
