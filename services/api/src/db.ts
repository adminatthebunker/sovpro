import pg from "pg";
import { config } from "./config.js";

const { Pool } = pg;

export const pool = new Pool({
  connectionString: config.databaseUrl,
  max: 10,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 5_000,
});

pool.on("error", (err: Error) => {
  console.error("pg pool error", err);
});

export type QueryArg = string | number | boolean | null | string[] | Record<string, unknown>;

export async function query<T = unknown>(text: string, params: QueryArg[] = []): Promise<T[]> {
  const res = await pool.query(text, params as unknown as unknown[]);
  return res.rows as T[];
}

export async function queryOne<T = unknown>(text: string, params: QueryArg[] = []): Promise<T | null> {
  const rows = await query<T>(text, params);
  return rows[0] ?? null;
}

export async function shutdown(): Promise<void> {
  await pool.end();
}
