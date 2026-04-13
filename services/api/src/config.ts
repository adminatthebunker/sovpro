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
  };
})();

export type Config = typeof config;
