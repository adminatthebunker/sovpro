import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { config } from "../config.js";
import { pool, query, queryOne } from "../db.js";
import { resolvePhotoUrl } from "../lib/photos.js";

// Instruction prefix for Qwen3-Embedding-0.6B query encoding.
// Indexing pipeline (scanner/src/legislative/speech_embedder.py) writes
// documents UNWRAPPED — this prefix is retrieval-time only. Omitting it
// drops NDCG@10 from 0.43 to 0.22 per services/embed/eval/REPORT.md.
const INSTRUCT_PREFIX =
  "Instruct: Given a parliamentary search query, retrieve relevant Canadian Hansard speech excerpts\nQuery: ";

// Shared filter fields for /speeches and /facets. Both handlers accept
// the same shape; /speeches adds page+limit on top.
const baseFilterSchema = z.object({
  q: z.string().trim().max(500).default(""),
  lang: z.enum(["en", "fr", "any"]).default("any"),
  level: z.enum(["federal", "provincial", "municipal"]).optional(),
  province_territory: z.string().length(2).optional(),
  politician_id: z.string().uuid().optional(),
  party: z.string().max(64).optional(),
  from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
});

const searchQuery = baseFilterSchema.extend({
  page: z.coerce.number().int().min(1).default(1),
  limit: z.coerce.number().int().min(1).max(50).default(20),
});

type BaseFilter = z.infer<typeof baseFilterSchema>;

interface SpeechSearchRow {
  chunk_id: string;
  speech_id: string;
  chunk_index: number;
  text: string;
  snippet_html: string | null;
  distance: number | null;
  spoken_at: string | null;
  language: "en" | "fr";
  level: string | null;
  province_territory: string | null;
  party_at_time: string | null;
  politician_id: string | null;
  politician_name: string | null;
  politician_slug: string | null;
  politician_photo_url: string | null;
  politician_photo_path: string | null;
  politician_party: string | null;
  politician_socials: Array<{ platform: string; url: string; handle: string | null }> | null;
  speech_speaker_name_raw: string;
  speech_source_url: string | null;
  speech_source_anchor: string | null;
  parliament_number: number | null;
  session_number: number | null;
}

async function encodeQuery(text: string): Promise<number[]> {
  const wrapped = `${INSTRUCT_PREFIX}${text}`;
  const res = await fetch(`${config.teiUrl}/embed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ inputs: [wrapped], normalize: true }),
  });
  if (!res.ok) {
    throw new Error(`TEI returned ${res.status}: ${await res.text().catch(() => "")}`);
  }
  const data: unknown = await res.json();
  // TEI default: bare [[...floats...]]. OpenAI-compat: {data: [{embedding: [...]}]}.
  if (Array.isArray(data) && Array.isArray((data as unknown[])[0])) {
    const first = (data as number[][])[0];
    if (first) return first;
  }
  if (data && typeof data === "object" && "data" in data) {
    const d = (data as { data: Array<{ embedding: number[] }> }).data;
    if (d?.[0]?.embedding) return d[0].embedding;
  }
  throw new Error("Unexpected TEI /embed response shape");
}

function toPgVector(vec: number[]): string {
  // pgvector literal: '[0.1,0.2,...]'. join with "," no spaces for tightness.
  return `[${vec.join(",")}]`;
}

/** Build the WHERE clause + filter params shared by /speeches and /facets.
 *  Returns filter-only params (no vector, no q-text). Callers append those
 *  at whatever $N index they need and pass the combined array to `query`. */
function buildFilterWhere(f: BaseFilter): {
  whereSql: string;
  filterParams: (string | number)[];
} {
  const where: string[] = ["sc.embedding IS NOT NULL"];
  const filterParams: (string | number)[] = [];
  if (f.lang !== "any") { filterParams.push(f.lang); where.push(`sc.language = $${filterParams.length}`); }
  if (f.level)          { filterParams.push(f.level); where.push(`sc.level = $${filterParams.length}`); }
  if (f.province_territory) { filterParams.push(f.province_territory); where.push(`sc.province_territory = $${filterParams.length}`); }
  if (f.politician_id)  { filterParams.push(f.politician_id); where.push(`sc.politician_id = $${filterParams.length}`); }
  if (f.party)          { filterParams.push(f.party); where.push(`sc.party_at_time = $${filterParams.length}`); }
  if (f.from)           { filterParams.push(f.from); where.push(`sc.spoken_at >= $${filterParams.length}`); }
  if (f.to)             { filterParams.push(f.to);   where.push(`sc.spoken_at < ($${filterParams.length}::date + interval '1 day')`); }
  return { whereSql: where.join(" AND "), filterParams };
}

function hasAnyStructuralFilter(f: BaseFilter): boolean {
  return Boolean(f.politician_id || f.party || f.level || f.province_territory || f.from || f.to);
}

export default async function searchRoutes(app: FastifyInstance) {
  app.get("/speeches", async (req, reply) => {
    const parsed = searchQuery.safeParse(req.query);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const { q, page, limit } = parsed.data;
    const offset = (page - 1) * limit;

    if (!q && !hasAnyStructuralFilter(parsed.data)) {
      return reply.badRequest("provide `q` or at least one filter (politician_id, party, level, province, from, to)");
    }

    const { whereSql, filterParams } = buildFilterWhere(parsed.data);
    const params: (string | number)[] = [...filterParams];
    let orderBy: string;
    let vectorParamIndex: number | null = null;
    if (q) {
      const vec = await encodeQuery(q);
      params.push(toPgVector(vec));
      vectorParamIndex = params.length;
      // Single-key ORDER BY: adding a tiebreaker (sc.id) forces Postgres to
      // materialise the full filtered set to satisfy deterministic sort,
      // defeating the HNSW index (8ms → 4400ms on 1.4M rows).
      orderBy = `sc.embedding <=> $${vectorParamIndex}::vector`;
    } else {
      orderBy = "sc.spoken_at DESC NULLS LAST, sc.id";
    }

    // ts_headline uses the per-row tsv_config so highlight tokenisation
    // matches the index used at build time.
    let headlineExpr = "NULL::text";
    if (q) {
      params.push(q);
      const qIdx = params.length;
      headlineExpr = `
        ts_headline(
          COALESCE(sc.tsv_config, 'simple')::regconfig,
          sc.text,
          websearch_to_tsquery(COALESCE(sc.tsv_config, 'simple')::regconfig, $${qIdx}),
          'MaxWords=35, MinWords=15, ShortWord=3, MaxFragments=2, FragmentDelimiter=" … ", HighlightAll=FALSE'
        )`;
    }

    // Cap the count at 1000 to avoid a full HNSW/filter scan just to
    // compute "pages". UIs beyond page 50 (at limit=20) are unusable
    // anyway; keyset pagination is a later concern.
    const COUNT_CAP = 1000;
    const countRow = await queryOne<{ n: number }>(
      `SELECT COUNT(*)::int AS n FROM (
         SELECT 1 FROM speech_chunks sc
         WHERE ${whereSql}
         LIMIT ${COUNT_CAP + 1}
       ) x`,
      filterParams,
    );
    const totalRaw = countRow?.n ?? 0;
    const totalCapped = totalRaw > COUNT_CAP;
    const total = totalCapped ? COUNT_CAP : totalRaw;

    const sql = `
      SELECT
        sc.id                         AS chunk_id,
        sc.speech_id,
        sc.chunk_index,
        sc.text,
        ${headlineExpr}               AS snippet_html,
        ${vectorParamIndex ? `(sc.embedding <=> $${vectorParamIndex}::vector)::float` : "NULL::float"} AS distance,
        sc.spoken_at,
        sc.language,
        sc.level,
        sc.province_territory,
        sc.party_at_time,
        sc.politician_id,
        p.name                        AS politician_name,
        p.openparliament_slug         AS politician_slug,
        p.photo_url                   AS politician_photo_url,
        p.photo_path                  AS politician_photo_path,
        p.party                       AS politician_party,
        socials.items                 AS politician_socials,
        s.speaker_name_raw            AS speech_speaker_name_raw,
        s.source_url                  AS speech_source_url,
        s.source_anchor               AS speech_source_anchor,
        ls.parliament_number,
        ls.session_number
      FROM speech_chunks sc
      LEFT JOIN politicians p           ON p.id  = sc.politician_id
      LEFT JOIN speeches   s            ON s.id  = sc.speech_id
      LEFT JOIN legislative_sessions ls ON ls.id = sc.session_id
      LEFT JOIN LATERAL (
        SELECT jsonb_agg(
                 jsonb_build_object('platform', ps.platform, 'url', ps.url, 'handle', ps.handle)
                 ORDER BY ps.platform
               ) AS items
          FROM politician_socials ps
         WHERE ps.politician_id = p.id
           AND COALESCE(ps.is_live, true)
      ) socials ON true
      WHERE ${whereSql}
      ORDER BY ${orderBy}
      LIMIT ${limit} OFFSET ${offset}
    `;

    const rows = await query<SpeechSearchRow>(sql, params);

    const items = rows.map((r) => ({
      chunk_id: r.chunk_id,
      speech_id: r.speech_id,
      chunk_index: r.chunk_index,
      text: r.text,
      snippet_html: r.snippet_html,
      similarity: r.distance !== null ? 1 - r.distance : null,
      spoken_at: r.spoken_at,
      language: r.language,
      level: r.level,
      province_territory: r.province_territory,
      party_at_time: r.party_at_time,
      politician: r.politician_id
        ? {
            id: r.politician_id,
            name: r.politician_name,
            slug: r.politician_slug,
            photo_url: resolvePhotoUrl({
              photo_path: r.politician_photo_path,
              photo_url: r.politician_photo_url,
            }),
            party: r.politician_party,
            socials: r.politician_socials ?? [],
          }
        : null,
      speech: {
        speaker_name_raw: r.speech_speaker_name_raw,
        source_url: r.speech_source_url,
        source_anchor: r.speech_source_anchor,
        session:
          r.parliament_number !== null && r.session_number !== null
            ? { parliament_number: r.parliament_number, session_number: r.session_number }
            : null,
      },
    }));

    return {
      items,
      page,
      limit,
      total,
      totalCapped,
      pages: Math.max(1, Math.ceil(total / limit)),
      mode: q ? "semantic" : "recent",
    };
  });

  app.get("/facets", async (req, reply) => {
    const parsed = baseFilterSchema.safeParse(req.query);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const { q } = parsed.data;

    if (!q && !hasAnyStructuralFilter(parsed.data)) {
      return reply.badRequest("provide `q` or at least one filter to aggregate");
    }

    const { whereSql, filterParams } = buildFilterWhere(parsed.data);
    const params: (string | number)[] = [...filterParams];

    // Top-N CTE: 200 semantic-ranked rows when q present, else recent.
    const ANALYSIS_LIMIT = 200;
    let topCte: string;
    let vectorParamIndex: number | null = null;
    if (q) {
      const vec = await encodeQuery(q);
      params.push(toPgVector(vec));
      vectorParamIndex = params.length;
      topCte = `
        SELECT sc.id, sc.politician_id, sc.party_at_time, sc.language,
               sc.spoken_at, sc.tsv, sc.tsv_config,
               sc.embedding <=> $${vectorParamIndex}::vector AS dist
          FROM speech_chunks sc
         WHERE ${whereSql}
         ORDER BY sc.embedding <=> $${vectorParamIndex}::vector
         LIMIT ${ANALYSIS_LIMIT}`;
    } else {
      topCte = `
        SELECT sc.id, sc.politician_id, sc.party_at_time, sc.language,
               sc.spoken_at, sc.tsv, sc.tsv_config,
               NULL::float AS dist
          FROM speech_chunks sc
         WHERE ${whereSql}
         ORDER BY sc.spoken_at DESC NULLS LAST, sc.id
         LIMIT ${ANALYSIS_LIMIT}`;
    }

    // Keyword-overlap needs the user's raw query text. Only appended when
    // q is present so numbering stays stable in the else branch.
    let keywordOverlapExpr = "NULL::jsonb";
    if (q) {
      params.push(q);
      const qIdx = params.length;
      keywordOverlapExpr = `
        (SELECT jsonb_build_object(
           'both', COUNT(*) FILTER (
              WHERE t.tsv @@ websearch_to_tsquery(COALESCE(t.tsv_config,'simple')::regconfig, $${qIdx})
           )::int,
           'semantic_only', COUNT(*) FILTER (
              WHERE NOT (t.tsv @@ websearch_to_tsquery(COALESCE(t.tsv_config,'simple')::regconfig, $${qIdx}))
           )::int
         ) FROM top t)`;
    }

    const sql = `
      WITH top AS (${topCte})
      SELECT
        (SELECT COUNT(*)::int FROM top)                                 AS analyzed_count,
        COALESCE((
          SELECT jsonb_agg(row_to_json(x))
            FROM (
              SELECT party_at_time AS party, COUNT(*)::int AS count,
                     ROUND(AVG(1 - dist)::numeric, 3)::float AS avg_similarity
                FROM top
               GROUP BY party_at_time
               ORDER BY count DESC
            ) x
        ), '[]'::jsonb)                                                  AS by_party,
        COALESCE((
          SELECT jsonb_agg(row_to_json(x))
            FROM (
              SELECT t.politician_id,
                     p.name                AS politician_name,
                     p.openparliament_slug AS politician_slug,
                     COUNT(*)::int         AS count,
                     ROUND(AVG(1 - COALESCE(t.dist, 0))::numeric, 3)::float AS avg_similarity
                FROM top t
                LEFT JOIN politicians p ON p.id = t.politician_id
               GROUP BY t.politician_id, p.name, p.openparliament_slug
               ORDER BY count DESC
               LIMIT 10
            ) x
        ), '[]'::jsonb)                                                  AS by_politician,
        COALESCE((
          SELECT jsonb_agg(row_to_json(x))
            FROM (
              SELECT EXTRACT(YEAR FROM spoken_at)::int AS year, COUNT(*)::int AS count
                FROM top
               WHERE spoken_at IS NOT NULL
               GROUP BY 1
               ORDER BY 1
            ) x
        ), '[]'::jsonb)                                                  AS by_year,
        COALESCE((
          SELECT jsonb_agg(row_to_json(x))
            FROM (
              SELECT language, COUNT(*)::int AS count
                FROM top
               GROUP BY language
            ) x
        ), '[]'::jsonb)                                                  AS by_language,
        ${keywordOverlapExpr}                                            AS keyword_overlap
    `;

    interface FacetsRow {
      analyzed_count: number;
      by_party: Array<{ party: string | null; count: number; avg_similarity: number }>;
      by_politician: Array<{
        politician_id: string | null;
        politician_name: string | null;
        politician_slug: string | null;
        count: number;
        avg_similarity: number;
      }>;
      by_year: Array<{ year: number; count: number }>;
      by_language: Array<{ language: "en" | "fr"; count: number }>;
      keyword_overlap: { both: number; semantic_only: number } | null;
    }

    // pgvector HNSW's default `ef_search=40` silently caps the candidate
    // set — a LIMIT 200 against the HNSW index returns only 40 rows
    // unless ef_search is raised. Wrap the facets query in a transaction
    // with SET LOCAL so the change scoped to this statement and doesn't
    // pollute pooled connections.
    const client = await pool.connect();
    let row: FacetsRow | null = null;
    try {
      await client.query("BEGIN");
      await client.query("SET LOCAL hnsw.ef_search = 300");
      try {
        const res = await client.query(sql, params as unknown as unknown[]);
        row = (res.rows[0] as FacetsRow) ?? null;
      } catch (err: unknown) {
        if (q) {
          app.log.warn({ err, q }, "facets query failed with q present; retrying without keyword_overlap");
          const retrySql = sql.replace(keywordOverlapExpr, "NULL::jsonb");
          const retryParams = params.slice(0, -1);
          const res = await client.query(retrySql, retryParams as unknown as unknown[]);
          row = (res.rows[0] as FacetsRow) ?? null;
        } else {
          throw err;
        }
      }
      await client.query("COMMIT");
    } catch (err) {
      await client.query("ROLLBACK").catch(() => {});
      throw err;
    } finally {
      client.release();
    }

    return {
      analyzed_count: row?.analyzed_count ?? 0,
      analysis_limit: ANALYSIS_LIMIT,
      by_party: row?.by_party ?? [],
      by_politician: (row?.by_politician ?? []).map((r) => ({
        politician: r.politician_id
          ? { id: r.politician_id, name: r.politician_name, slug: r.politician_slug }
          : null,
        count: r.count,
        avg_similarity: r.avg_similarity,
      })),
      by_year: row?.by_year ?? [],
      by_language: row?.by_language ?? [],
      keyword_overlap: row?.keyword_overlap ?? null,
      mode: q ? "semantic" : "recent",
    };
  });

  app.get("/meta", async () => {
    // Backfill progress surface for the UI banner.
    const row = await queryOne<{ total: number; embedded: number }>(
      `SELECT COUNT(*)::int AS total,
              COUNT(*) FILTER (WHERE embedding IS NOT NULL)::int AS embedded
         FROM speech_chunks`,
    );
    const total = row?.total ?? 0;
    const embedded = row?.embedded ?? 0;
    return {
      total_chunks: total,
      embedded_chunks: embedded,
      coverage: total > 0 ? embedded / total : 0,
    };
  });
}
