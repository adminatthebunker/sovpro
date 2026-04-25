import type { FastifyInstance } from "fastify";
import { query, queryOne } from "../db.js";

// Progressive-mirror cache for federal MP detail from openparliament.ca.
//
// On first visit to an MP's profile, the frontend calls this endpoint, which:
//   1. Returns cached data if fresh.
//   2. Otherwise fetches from api.openparliament.ca, upserts the cache, and
//      returns the new data.
//   3. On outbound failure with an available stale cache row, serves stale
//      data with a warning so the profile page keeps working.
//
// The slug column is populated by the scanner-side resolver
// (resolve_openparliament.py) — non-federal politicians and federal MPs
// whose slug we haven't matched yet return 204 here.

const OPENPARL_BASE = "https://api.openparliament.ca";
const OPENPARL_USER_AGENT =
  "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca; admin@thebunkerops.ca)";
const FETCH_TIMEOUT_MS = 5_000;
const DEFAULT_TTL_DAYS = 30;
// Activity (speeches + bills) changes daily — cache for 1 day.
const ACTIVITY_TTL_MS = 24 * 3600 * 1000;
// Cap how much we pull from openparliament per refresh. Keeps payloads
// small and stays well under their rate limits.
const ACTIVITY_SPEECH_LIMIT = 20;
const ACTIVITY_BILL_LIMIT = 10;

type Envelope = {
  source: "cache" | "fresh" | "stale";
  fetched_at: string;
  expires_at: string;
  warning?: string;
  data: unknown;
};

type PoliticianRow = {
  id: string;
  level: string;
  openparliament_slug: string | null;
};

type CacheRow = {
  politician_id: string;
  slug: string;
  data: unknown;
  fetched_at: Date;
  expires_at: Date;
};

type ActivityCacheRow = {
  politician_id: string;
  slug: string;
  activity_data: unknown;
  activity_fetched_at: Date | null;
  activity_expires_at: Date | null;
};

type ActivityPayload = {
  speeches: unknown[];
  bills: unknown[];
};

// Coalesce concurrent outbound fetches per (kind, slug) tuple so a thundering
// herd of cache-miss requests fires only one HTTP call per kind to
// openparliament.ca. Single API container today; a Map is enough.
const inflight = new Map<string, Promise<unknown>>();

async function fetchOpenparliament(path: string, cacheKey: string): Promise<unknown> {
  const existing = inflight.get(cacheKey);
  if (existing) return existing;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  const promise = (async () => {
    try {
      const res = await fetch(`${OPENPARL_BASE}${path}`, {
        method: "GET",
        headers: {
          Accept: "application/json",
          "API-Version": "v1",
          "User-Agent": OPENPARL_USER_AGENT,
        },
        signal: ctrl.signal,
      });
      if (!res.ok) {
        throw new Error(`openparliament ${res.status}: ${res.statusText}`);
      }
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  })();

  inflight.set(cacheKey, promise);
  try {
    return await promise;
  } finally {
    inflight.delete(cacheKey);
  }
}

async function fetchDetail(slug: string): Promise<unknown> {
  return fetchOpenparliament(
    `/politicians/${encodeURIComponent(slug)}/`,
    `detail:${slug}`
  );
}

async function fetchActivity(slug: string): Promise<ActivityPayload> {
  const [speechesRes, billsRes] = await Promise.all([
    fetchOpenparliament(
      `/speeches/?politician=${encodeURIComponent(slug)}&limit=${ACTIVITY_SPEECH_LIMIT}&format=json`,
      `speeches:${slug}`
    ) as Promise<{ objects?: unknown[] }>,
    fetchOpenparliament(
      `/bills/?sponsor_politician=${encodeURIComponent(slug)}&limit=${ACTIVITY_BILL_LIMIT}&format=json`,
      `bills:${slug}`
    ) as Promise<{ objects?: unknown[] }>,
  ]);
  return {
    speeches: speechesRes.objects ?? [],
    bills: billsRes.objects ?? [],
  };
}

export default async function openparliamentRoutes(app: FastifyInstance) {
  // Per-politician speeches + sponsored bills feed. Same 404/400/204 shape
  // as the detail endpoint; serves stale cache on upstream failures.
  app.get<{ Params: { id: string } }>("/:id/parliament-activity", async (req, reply) => {
    const { id } = req.params;
    const politician = await queryOne<PoliticianRow>(
      `SELECT id, level, openparliament_slug
         FROM politicians
        WHERE id = $1`,
      [id]
    );
    if (!politician) return reply.notFound("Politician not found");
    if (politician.level !== "federal") {
      return reply.badRequest("openparliament.ca only covers federal MPs");
    }
    if (!politician.openparliament_slug) return reply.code(204).send();

    const slug = politician.openparliament_slug;

    const cached = await queryOne<ActivityCacheRow>(
      `SELECT politician_id, slug, activity_data, activity_fetched_at, activity_expires_at
         FROM politician_openparliament_cache
        WHERE politician_id = $1`,
      [id]
    );
    const now = Date.now();
    if (
      cached?.activity_data &&
      cached.activity_expires_at &&
      cached.activity_expires_at.getTime() > now
    ) {
      return {
        source: "cache" as const,
        fetched_at: cached.activity_fetched_at?.toISOString() ?? null,
        expires_at: cached.activity_expires_at.toISOString(),
        data: cached.activity_data,
      };
    }

    try {
      const activity = await fetchActivity(slug);
      const expiresAt = new Date(now + ACTIVITY_TTL_MS);
      // Upsert. If the cache row doesn't exist yet (detail hasn't been
      // fetched), insert a minimal row with empty detail so the PK constraint
      // is satisfied — detail endpoint will hydrate it on next hit.
      await query(
        `INSERT INTO politician_openparliament_cache
           (politician_id, slug, data, fetched_at, expires_at,
            activity_data, activity_fetched_at, activity_expires_at,
            activity_last_error, activity_last_error_at)
         VALUES ($1, $2, '{}'::jsonb, now(), now() + interval '30 days',
                 $3, now(), $4, NULL, NULL)
         ON CONFLICT (politician_id) DO UPDATE SET
           activity_data = EXCLUDED.activity_data,
           activity_fetched_at = EXCLUDED.activity_fetched_at,
           activity_expires_at = EXCLUDED.activity_expires_at,
           activity_last_error = NULL,
           activity_last_error_at = NULL`,
        [id, slug, JSON.stringify(activity), expiresAt.toISOString()]
      );
      return {
        source: "fresh" as const,
        fetched_at: new Date(now).toISOString(),
        expires_at: expiresAt.toISOString(),
        data: activity,
      };
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      app.log.warn({ slug, err: errMsg }, "openparliament activity fetch failed");
      await query(
        `UPDATE politician_openparliament_cache
            SET activity_last_error = $2, activity_last_error_at = now()
          WHERE politician_id = $1`,
        [id, errMsg]
      );
      if (cached?.activity_data) {
        return {
          source: "stale" as const,
          fetched_at: cached.activity_fetched_at?.toISOString() ?? null,
          expires_at: cached.activity_expires_at?.toISOString() ?? null,
          warning: `Serving stale activity: upstream fetch failed (${errMsg})`,
          data: cached.activity_data,
        };
      }
      return reply
        .code(503)
        .header("retry-after", "60")
        .send({ error: "openparliament.ca unavailable", detail: errMsg });
    }
  });

  app.get<{ Params: { id: string } }>("/:id/openparliament", async (req, reply) => {
    const { id } = req.params;

    const politician = await queryOne<PoliticianRow>(
      `SELECT id, level, openparliament_slug
         FROM politicians
        WHERE id = $1`,
      [id]
    );
    if (!politician) return reply.notFound("Politician not found");
    if (politician.level !== "federal") {
      return reply.badRequest("openparliament.ca only covers federal MPs");
    }
    if (!politician.openparliament_slug) {
      // Federal MP but we haven't resolved their openparliament slug yet
      // (or they aren't indexed there — e.g. fresh by-election winner).
      return reply.code(204).send();
    }

    const slug = politician.openparliament_slug;

    // Check cache first.
    const cached = await queryOne<CacheRow>(
      `SELECT politician_id, slug, data, fetched_at, expires_at
         FROM politician_openparliament_cache
        WHERE politician_id = $1`,
      [id]
    );
    const now = Date.now();
    if (cached && cached.expires_at.getTime() > now) {
      const env: Envelope = {
        source: "cache",
        fetched_at: cached.fetched_at.toISOString(),
        expires_at: cached.expires_at.toISOString(),
        data: cached.data,
      };
      reply.header("cache-control", "public, max-age=300");
      return env;
    }

    // Cache miss or expired — fetch fresh.
    try {
      const data = await fetchDetail(slug);
      const expiresAt = new Date(now + DEFAULT_TTL_DAYS * 24 * 3600 * 1000);
      await query(
        `INSERT INTO politician_openparliament_cache
           (politician_id, slug, data, fetched_at, expires_at, last_error, last_error_at)
         VALUES ($1, $2, $3, now(), $4, NULL, NULL)
         ON CONFLICT (politician_id) DO UPDATE SET
           slug = EXCLUDED.slug,
           data = EXCLUDED.data,
           fetched_at = EXCLUDED.fetched_at,
           expires_at = EXCLUDED.expires_at,
           last_error = NULL,
           last_error_at = NULL`,
        [id, slug, JSON.stringify(data), expiresAt.toISOString()]
      );
      const env: Envelope = {
        source: "fresh",
        fetched_at: new Date(now).toISOString(),
        expires_at: expiresAt.toISOString(),
        data,
      };
      reply.header("cache-control", "public, max-age=300");
      return env;
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      app.log.warn({ slug, err: errMsg }, "openparliament fetch failed");

      // Record the failure — useful for observability + keeps the stale
      // cache row queryable so we know it's been failing.
      await query(
        `UPDATE politician_openparliament_cache
            SET last_error = $2, last_error_at = now()
          WHERE politician_id = $1`,
        [id, errMsg]
      );

      // Serve stale cache if we have one.
      if (cached) {
        const env: Envelope = {
          source: "stale",
          fetched_at: cached.fetched_at.toISOString(),
          expires_at: cached.expires_at.toISOString(),
          warning: `Serving stale data: upstream fetch failed (${errMsg})`,
          data: cached.data,
        };
        return env;
      }

      // No cache to fall back on.
      return reply
        .code(503)
        .header("retry-after", "60")
        .send({ error: "openparliament.ca unavailable", detail: errMsg });
    }
  });
}
