import type { FastifyInstance } from "fastify";
import { createHmac, timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { config } from "../config.js";
import { query, queryOne } from "../db.js";

/**
 * Per-saved-search RSS feeds. A feed URL like
 *   /api/v1/feeds/<hmac-token>.rss
 * is stable, cookie-less, and safe to paste into any feed reader.
 *
 * Token: HMAC-SHA256(JWT_SECRET, "feed:" + saved_search_id) → hex.
 * Same secret as the unsubscribe tokens, different message prefix so
 * the two purposes can never be confused.
 *
 * Ordering: top 200 by semantic distance (if the saved search has a
 * query_embedding), then sort those 200 by spoken_at DESC and return
 * the 50 most recent. Feed readers show a chronological stream, so
 * recency wins over distance — but we still restrict to the semantic
 * neighborhood so `q=carbon tax` doesn't devolve into "whatever was
 * said today regardless of topic". Filter-only saved searches skip the
 * semantic stage and just return the 50 most recent filter matches.
 */

const paramSchema = z.object({
  token: z.string().regex(/^[0-9a-f]{64}$/),
});

function verifyFeedToken(token: string, savedSearchId: string): boolean {
  if (!config.jwtSecret) return false;
  const expected = createHmac("sha256", config.jwtSecret)
    .update(`feed:${savedSearchId}`)
    .digest();
  let received: Buffer;
  try {
    received = Buffer.from(token, "hex");
  } catch {
    return false;
  }
  if (received.length !== expected.length) return false;
  return timingSafeEqual(expected, received);
}

/**
 * Same linear-scan strategy as alerts.ts findSavedSearchByToken — at
 * phase-2 scale this is O(N) across saved_searches, which is trivial;
 * if we ever have >10k active feeds we'd switch the URL format to
 * `<id>.<hmac>.rss` to short-circuit the lookup.
 */
async function findSavedSearchByFeedToken(token: string): Promise<string | null> {
  if (!config.jwtSecret) return null;
  const rows = await query<{ id: string }>(`SELECT id FROM saved_searches`);
  for (const row of rows) {
    if (verifyFeedToken(token, row.id)) return row.id;
  }
  return null;
}

interface SavedSearchForFeed {
  id: string;
  name: string;
  filter_payload: Record<string, unknown>;
  query_embedding: string | null;   // asyncpg returns pgvector as its text form
  created_at: string;
  updated_at: string;
}

interface FeedMatch {
  chunk_id: string;
  speech_id: string;
  text: string;
  spoken_at: string | null;
  source_url: string | null;
  speaker_name_raw: string | null;
  politician_name: string | null;
}

/**
 * Build the shared filter WHERE clause, mirroring the Python
 * alerts_worker._build_filter_sql but against the speech_chunks table
 * (so column prefixes are `sc.`).
 */
function buildFilterWhere(
  payload: Record<string, unknown>,
  paramOffset: number,
): { sql: string; params: (string | number)[] } {
  const clauses: string[] = [];
  const params: (string | number)[] = [];
  const push = (col: string, val: string) => {
    params.push(val);
    clauses.push(`${col} = $${paramOffset + params.length}`);
  };
  const lang = payload.lang;
  if (typeof lang === "string" && lang !== "any") push("sc.language", lang);
  const level = payload.level;
  if (typeof level === "string") push("sc.level", level);
  const pt = payload.province_territory;
  if (typeof pt === "string") push("sc.province_territory", pt);
  const pol = payload.politician_id;
  if (typeof pol === "string") push("sc.politician_id", pol);
  const party = payload.party;
  if (typeof party === "string") push("sc.party_at_time", party);
  const from = payload.from;
  if (typeof from === "string") {
    params.push(from);
    clauses.push(`sc.spoken_at >= $${paramOffset + params.length}`);
  }
  const to = payload.to;
  if (typeof to === "string") {
    params.push(to);
    clauses.push(`sc.spoken_at < ($${paramOffset + params.length}::date + interval '1 day')`);
  }
  return {
    sql: clauses.length > 0 ? " AND " + clauses.join(" AND ") : "",
    params,
  };
}

async function runFeedMatch(ss: SavedSearchForFeed): Promise<FeedMatch[]> {
  const { sql: filterSql, params: filterParams } = buildFilterWhere(
    ss.filter_payload,
    ss.query_embedding ? 1 : 0
  );

  if (ss.query_embedding) {
    // Two-stage: top-200 by semantic distance, then re-sort by recency.
    const params: unknown[] = [ss.query_embedding, ...filterParams];
    const s = `
      WITH candidates AS (
        SELECT sc.id AS chunk_id, sc.speech_id, sc.text, sc.spoken_at,
               sc.politician_id
          FROM speech_chunks sc
         WHERE sc.embedding IS NOT NULL
           ${filterSql}
         ORDER BY sc.embedding <=> $1::vector
         LIMIT 200
      )
      SELECT c.chunk_id, c.speech_id, c.text, c.spoken_at,
             s.source_url, s.speaker_name_raw,
             p.name AS politician_name
        FROM candidates c
        JOIN speeches s ON s.id = c.speech_id
        LEFT JOIN politicians p ON p.id = c.politician_id
       ORDER BY c.spoken_at DESC NULLS LAST
       LIMIT 50
    `;
    return await query<FeedMatch>(s, params as never);
  }

  // Filter-only: 50 most recent speeches matching the filters.
  const s = `
    SELECT NULL::uuid AS chunk_id, s.id AS speech_id, s.text, s.spoken_at,
           s.source_url, s.speaker_name_raw,
           p.name AS politician_name
      FROM speeches s
      LEFT JOIN politicians p ON p.id = s.politician_id
     WHERE 1=1
       ${filterSql.replace(/sc\./g, "s.")}
     ORDER BY s.spoken_at DESC NULLS LAST
     LIMIT 50
  `;
  return await query<FeedMatch>(s, filterParams as never);
}

function xmlEscape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function rssDate(iso: string | null): string {
  if (!iso) return new Date().toUTCString();
  return new Date(iso).toUTCString();
}

function renderRss(ss: SavedSearchForFeed, matches: FeedMatch[]): string {
  const feedUrl = `${config.publicSiteUrl}/api/v1/feeds/${feedToken(ss.id)}.rss`;
  const siteUrl = config.publicSiteUrl;
  const title = `CPD: ${ss.name}`;
  const desc = `New Canadian political speeches matching "${ss.name}" from Canadian Political Data.`;

  const items = matches.map(m => {
    const speaker =
      m.politician_name || m.speaker_name_raw || "Unknown speaker";
    const snippet = (m.text || "").trim().slice(0, 800);
    const itemTitle = `${rssDate(m.spoken_at).slice(0, 16)} · ${speaker}`;
    const link = m.source_url || `${siteUrl}/speeches/${m.speech_id}`;
    // GUID: chunk_id if available, otherwise speech_id — stable per-item.
    const guid = m.chunk_id || m.speech_id;
    return `    <item>
      <title>${xmlEscape(itemTitle)}</title>
      <link>${xmlEscape(link)}</link>
      <guid isPermaLink="false">${xmlEscape(guid)}</guid>
      <pubDate>${rssDate(m.spoken_at)}</pubDate>
      <description>${xmlEscape(snippet)}</description>
    </item>`;
  }).join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>${xmlEscape(title)}</title>
    <link>${xmlEscape(siteUrl)}/account/saved-searches</link>
    <atom:link href="${xmlEscape(feedUrl)}" rel="self" type="application/rss+xml" />
    <description>${xmlEscape(desc)}</description>
    <language>en-ca</language>
    <lastBuildDate>${new Date().toUTCString()}</lastBuildDate>
${items}
  </channel>
</rss>
`;
}

/** Generate a feed token for a saved search id (callable by other routes). */
export function feedToken(savedSearchId: string): string {
  if (!config.jwtSecret) return "";
  return createHmac("sha256", config.jwtSecret)
    .update(`feed:${savedSearchId}`)
    .digest("hex");
}

export default async function feedRoutes(app: FastifyInstance) {
  // ── GET /feeds/:token.rss ────────────────────────────────────
  app.get<{ Params: { token: string } }>(
    "/:token.rss",
    {
      config: { rateLimit: { max: 60, timeWindow: "1 minute" } },
    },
    async (req, reply) => {
      const parsed = paramSchema.safeParse(req.params);
      if (!parsed.success) {
        return reply.code(400).send({ error: "invalid token" });
      }

      const savedSearchId = await findSavedSearchByFeedToken(parsed.data.token);
      if (!savedSearchId) {
        return reply.code(404).send({ error: "feed not found" });
      }

      const ss = await queryOne<SavedSearchForFeed>(
        `SELECT id, name, filter_payload,
                CASE WHEN query_embedding IS NOT NULL
                     THEN query_embedding::text
                     ELSE NULL END AS query_embedding,
                created_at, updated_at
           FROM saved_searches WHERE id = $1`,
        [savedSearchId]
      );
      if (!ss) return reply.code(404).send({ error: "feed not found" });

      const matches = await runFeedMatch(ss);
      const xml = renderRss(ss, matches);
      reply
        .header("Content-Type", "application/rss+xml; charset=utf-8")
        .header("Cache-Control", "public, max-age=600")
        .send(xml);
    }
  );
}
