import { z } from "zod";

const schema = z.object({
  NODE_ENV: z.enum(["development", "production", "test"]).default("production"),
  API_PORT: z.coerce.number().int().default(3000),
  API_HOST: z.string().default("0.0.0.0"),
  API_LOG_LEVEL: z.enum(["trace","debug","info","warn","error","fatal"]).default("info"),
  // CORS allowlist. Default is the production origin — NOT a
  // wildcard. Setting this to "*" with credentials: true in
  // services/api/src/index.ts would be browser-rejected for
  // credentialed cross-origin responses anyway, but it's sloppy
  // signalling. Comma-separated list allowed (e.g. for
  // "prod + www subdomain" setups); @fastify/cors accepts an
  // array. In dev, set API_CORS_ORIGIN=http://localhost:5173 in
  // your local .env.
  API_CORS_ORIGIN: z.string().default("https://canadianpoliticaldata.ca"),
  DATABASE_URL: z.string().url().or(z.string().startsWith("postgres")),
  CHANGE_WEBHOOK_SECRET: z.string().min(16).optional(),
  WEBHOOK_SECRET: z.string().min(16).optional(),
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
  // OpenRouter (AI contradictions analysis on grouped search).
  // Unset OPENROUTER_API_KEY → feature disabled; GET /contradictions/meta
  // returns { enabled: false } and POST /analyze returns 503. The model id
  // is surfaced to the frontend consent modal verbatim, so swapping it
  // (e.g. when a free-tier option disappears) is a one-line ops change
  // that re-prompts every user on their next click.
  OPENROUTER_API_KEY: z.string().optional(),
  OPENROUTER_MODEL: z.string().default("nvidia/nemotron-3-super-120b-a12b:free"),
  OPENROUTER_BASE_URL: z.string().url().default("https://openrouter.ai/api/v1"),
  OPENROUTER_SITE_URL: z.string().url().default("https://canadianpoliticaldata.ca"),
  OPENROUTER_APP_NAME: z.string().default("Canadian Political Data"),
  OPENROUTER_TIMEOUT_MS: z.coerce.number().int().positive().default(30000),
  // Stripe billing rail (premium-reports phase 1a + future dev-API
  // subscriptions). Unset STRIPE_SECRET_KEY → POST /me/credits/checkout
  // returns 503 and the "Buy credits" UI hides its purchase buttons.
  // Webhook signature verification requires STRIPE_WEBHOOK_SECRET; an
  // unset secret causes POST /webhooks/stripe to refuse every event
  // (fail-closed, not fail-open). Price IDs are created once in the
  // Stripe dashboard; each one that's unset hides its pack on the
  // frontend pack listing. Success / cancel URLs fall back to
  // ${PUBLIC_SITE_URL}/account/credits?purchase=success|cancel.
  STRIPE_SECRET_KEY: z.string().optional(),
  STRIPE_WEBHOOK_SECRET: z.string().optional(),
  STRIPE_PRICE_ID_CREDIT_PACK_SMALL: z.string().optional(),
  STRIPE_PRICE_ID_CREDIT_PACK_MEDIUM: z.string().optional(),
  STRIPE_PRICE_ID_CREDIT_PACK_LARGE: z.string().optional(),
  // Preprocess empty string → undefined so docker-compose's
  // `${VAR:-}` pattern (empty string when unset) doesn't trip .url()
  // validation. Consistent with how the other optional strings
  // behave under the same passthrough.
  STRIPE_SUCCESS_URL: z
    .preprocess((v) => (v === "" ? undefined : v), z.string().url().optional()),
  STRIPE_CANCEL_URL: z
    .preprocess((v) => (v === "" ? undefined : v), z.string().url().optional()),
  // Credits granted to a user whose correction transitions to
  // status='applied'. See docs/plans/premium-reports.md (correction
  // rewards section) for the rationale; tune this value without code
  // changes by setting CORRECTION_REWARD_CREDITS in .env.
  CORRECTION_REWARD_CREDITS: z.coerce.number().int().min(0).default(10),
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
    // Comma-separated list → array for @fastify/cors. Single entry
    // stays a string so the normal single-origin path is unchanged.
    corsOrigin: (() => {
      const raw = env.API_CORS_ORIGIN;
      if (raw.includes(",")) {
        return raw.split(",").map((s) => s.trim()).filter(Boolean);
      }
      return raw;
    })(),
    databaseUrl: env.DATABASE_URL,
    webhookSecret: env.CHANGE_WEBHOOK_SECRET ?? env.WEBHOOK_SECRET ?? "",
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
    openrouter: {
      apiKey: env.OPENROUTER_API_KEY ?? "",
      model: env.OPENROUTER_MODEL,
      baseUrl: env.OPENROUTER_BASE_URL.replace(/\/$/, ""),
      siteUrl: env.OPENROUTER_SITE_URL.replace(/\/$/, ""),
      appName: env.OPENROUTER_APP_NAME,
      timeoutMs: env.OPENROUTER_TIMEOUT_MS,
      enabled: (env.OPENROUTER_API_KEY ?? "").length > 0,
    },
    stripe: {
      secretKey: env.STRIPE_SECRET_KEY ?? "",
      webhookSecret: env.STRIPE_WEBHOOK_SECRET ?? "",
      priceIds: {
        small: env.STRIPE_PRICE_ID_CREDIT_PACK_SMALL ?? "",
        medium: env.STRIPE_PRICE_ID_CREDIT_PACK_MEDIUM ?? "",
        large: env.STRIPE_PRICE_ID_CREDIT_PACK_LARGE ?? "",
      },
      successUrl:
        env.STRIPE_SUCCESS_URL ??
        `${env.PUBLIC_SITE_URL.replace(/\/$/, "")}/account/credits?purchase=success`,
      cancelUrl:
        env.STRIPE_CANCEL_URL ??
        `${env.PUBLIC_SITE_URL.replace(/\/$/, "")}/account/credits?purchase=cancel`,
      // Feature-level enabled flag: both the SDK key and the webhook
      // secret must be set for Checkout to round-trip. Price IDs are
      // per-pack — see packs() below.
      enabled:
        (env.STRIPE_SECRET_KEY ?? "").length > 0 &&
        (env.STRIPE_WEBHOOK_SECRET ?? "").length > 0,
    },
    corrections: {
      rewardCredits: env.CORRECTION_REWARD_CREDITS,
    },
  };
})();

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

if (config.env === "production" && !config.stripe.enabled) {
  console.warn(
    "[config] Stripe secret key or webhook secret unset in production; /me/credits/checkout and /webhooks/stripe will be disabled."
  );
}

export type Config = typeof config;
