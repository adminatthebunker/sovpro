import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { config } from "../config.js";
import { pool, query, queryOne } from "../db.js";
import { resolvePhotoUrl } from "../lib/photos.js";
import { requireUser, getUser } from "../middleware/user-auth.js";

// Instruction prefix for Qwen3-Embedding-0.6B query encoding.
// Indexing pipeline (scanner/src/legislative/speech_embedder.py) writes
// documents UNWRAPPED — this prefix is retrieval-time only. Omitting it
// drops NDCG@10 from 0.43 to 0.22 per services/embed/eval/REPORT.md.
const INSTRUCT_PREFIX =
  "Instruct: Given a parliamentary search query, retrieve relevant Canadian Hansard speech excerpts\nQuery: ";

// Shared filter fields for /speeches and /facets. Both handlers accept
// the same shape; /speeches adds page+limit on top.
// Exported so /me/saved-searches can reuse the exact validation shape —
// single source of truth for "what's a valid search".
// `politician_id` is the legacy singular field; `politician_ids` is the
// canonical multi-select form. Both are accepted for backward compat
// (existing URLs, already-stored saved_searches rows) and collapsed via
// `effectivePoliticianIds()` at SQL-build time. New writes should use
// `politician_ids` exclusively.
// Fastify parses repeated URL params (`?politician_id=a&politician_id=b`)
// as a string[]. Accept either form and let effectivePoliticianIds()
// collapse it downstream — keeps the URL convention ergonomic without
// forcing every caller to know about politician_ids.
const politicianIdInput = z.union([
  z.string().uuid(),
  z.array(z.string().uuid()).max(10),
]).optional();

export const baseFilterSchema = z.object({
  q: z.string().trim().max(500).default(""),
  lang: z.enum(["en", "fr", "any"]).default("any"),
  level: z.enum(["federal", "provincial", "municipal"]).optional(),
  province_territory: z.string().length(2).optional(),
  politician_id: politicianIdInput,
  politician_ids: z.array(z.string().uuid()).max(10).optional(),
  party: z.string().max(64).optional(),
  from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  exclude_presiding: z.coerce.boolean().optional(),
});

export function effectivePoliticianIds(
  f: Pick<z.infer<typeof baseFilterSchema>, "politician_id" | "politician_ids">
): string[] {
  const ids: string[] = [];
  if (f.politician_ids) ids.push(...f.politician_ids);
  if (f.politician_id) {
    if (Array.isArray(f.politician_id)) ids.push(...f.politician_id);
    else ids.push(f.politician_id);
  }
  // Dedupe, cap at 10.
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of ids) {
    if (!seen.has(id) && out.length < 10) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

const searchQuery = baseFilterSchema.extend({
  page: z.coerce.number().int().min(1).default(1),
  limit: z.coerce.number().int().min(1).max(50).default(20),
  // Rendering mode: flat chunk list (default) or one politician per card
  // with their top-N matching chunks underneath. Grouped mode requires
  // `q` because grouping only makes sense when ranked by semantic
  // relevance — a q-less grouped call is a 400.
  group_by: z.enum(["timeline", "politician"]).default("timeline"),
  per_group_limit: z.coerce.number().int().min(1).max(10).default(5),
  // Grouped-mode-only: which per-politician metric decides the top-20.
  // Ignored for group_by=timeline. Default `mentions` answers "who talks
  // about this topic the most" — matches user intuition and the Analysis
  // tab's TOP SPEAKERS list.
  sort: z.enum(["mentions", "best_match", "avg_match", "keyword_hits"]).default("mentions"),
});

// Single-politician deep-dive ("show all of X's quotes for query Q").
// Authenticated, rate-limited surface backing the expand-card affordance
// on /search. Extends baseFilterSchema rather than forking it so saved
// filters and pin shares stay compatible. politician_id is overridden to
// required + single-UUID — the multi-pin form doesn't fit the deep-dive
// UX, and a missing id would silently fall through to a global search.
const expandQuery = baseFilterSchema.extend({
  politician_id: z.string().uuid(),
  page: z.coerce.number().int().min(1).default(1),
  limit: z.coerce.number().int().min(1).max(50).default(50),
  // Client-selectable similarity floor. Always clamped >= 0.45 server-side
  // so the baseline "actually matches the query" definition never weakens;
  // tightening is allowed (≥50%, ≥60%, ≥70%, ≥80%).
  min_similarity: z.coerce.number().min(0).max(1).optional(),
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
  speech_speaker_role: string | null;
  speech_source_url: string | null;
  speech_source_anchor: string | null;
  parliament_number: number | null;
  session_number: number | null;
  // Per-politician aggregates repeated on every chunk row of a group —
  // the grouping walker just reads them off the first row it sees for
  // each politician_id.
  mention_count?: number;
  best_dist?: number | null;
  avg_dist?: number | null;
  keyword_hits?: number;
}

export async function encodeQuery(text: string): Promise<number[]> {
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

export function toPgVector(vec: number[]): string {
  // pgvector literal: '[0.1,0.2,...]'. join with "," no spaces for tightness.
  return `[${vec.join(",")}]`;
}

/** Build the WHERE clause + filter params shared by /speeches and /facets.
 *  Returns filter-only params (no vector, no q-text). Callers append those
 *  at whatever $N index they need and pass the combined array to `query`. */
function buildFilterWhere(f: BaseFilter): {
  whereSql: string;
  filterParams: (string | number | string[])[];
} {
  const where: string[] = ["sc.embedding IS NOT NULL"];
  const filterParams: (string | number | string[])[] = [];
  if (f.lang !== "any") { filterParams.push(f.lang); where.push(`sc.language = $${filterParams.length}`); }
  if (f.level)          { filterParams.push(f.level); where.push(`sc.level = $${filterParams.length}`); }
  if (f.province_territory) { filterParams.push(f.province_territory); where.push(`sc.province_territory = $${filterParams.length}`); }
  const pids = effectivePoliticianIds(f);
  if (pids.length > 0) { filterParams.push(pids); where.push(`sc.politician_id = ANY($${filterParams.length}::uuid[])`); }
  if (f.party)          { filterParams.push(f.party); where.push(`sc.party_at_time = $${filterParams.length}`); }
  if (f.from)           { filterParams.push(f.from); where.push(`sc.spoken_at >= $${filterParams.length}`); }
  if (f.to)             { filterParams.push(f.to);   where.push(`sc.spoken_at < ($${filterParams.length}::date + interval '1 day')`); }
  // Hide presiding-officer turns ("I declare the motion lost", procedural
  // chair speech). Correlated EXISTS scoped to the HNSW candidate pool —
  // negligible cost since the WHERE caps at ~1k chunks.
  if (f.exclude_presiding) {
    where.push(
      `NOT EXISTS (SELECT 1 FROM speeches sx WHERE sx.id = sc.speech_id AND sx.speaker_role IS NOT NULL AND sx.speaker_role <> '')`,
    );
  }
  return { whereSql: where.join(" AND "), filterParams };
}

function hasAnyStructuralFilter(f: BaseFilter): boolean {
  return Boolean(
    effectivePoliticianIds(f).length > 0 ||
    f.party || f.level || f.province_territory || f.from || f.to
  );
}

type SearchInput = z.infer<typeof searchQuery>;

/** Grouped-by-politician search: return top-K politicians, each with their
 *  top-M chunks on the query, so readers can see one politician's statements
 *  on a topic side-by-side. The core bet: seeing a politician's quotes across
 *  parliaments makes contradictions or evolution visible without any AI
 *  claim. Requires `q` — grouping a recency-ordered result would just be
 *  "whichever politicians spoke most recently", which isn't interesting. */
async function handleGroupedByPolitician(
  app: FastifyInstance,
  reply: import("fastify").FastifyReply,
  input: SearchInput,
) {
  const { q, page, limit, per_group_limit, sort } = input;
  if (!q) {
    return reply.badRequest("group_by=politician requires a semantic query (`q`)");
  }

  const { whereSql, filterParams } = buildFilterWhere(input);
  const vec = await encodeQuery(q);
  const vecLiteral = toPgVector(vec);

  // Cap politicians per page at 20 regardless of user-supplied limit — the
  // UI renders one card per politician and larger pages get unusable.
  const politicianLimit = Math.min(limit, 20);
  const politicianOffset = (page - 1) * politicianLimit;
  // Pulling 1000 chunk candidates so `mentions`/`keyword_hits` counts are
  // meaningful, not just a function of a too-tight top-500 window.
  // pgvector 0.8.2 caps hnsw.ef_search at 1000; CANDIDATE_POOL tracks
  // that ceiling — exceeding it would silently truncate recall anyway.
  const CANDIDATE_POOL = 1000;
  // Distance threshold for counting a chunk as a "mention" — below this
  // similarity (0.45) Qwen3 results start drifting off-topic for the
  // civic-Hansard corpus. Tuned by eye; revisit if recall complaints.
  const MIN_SIMILARITY = 0.45;
  const MAX_DISTANCE = 1 - MIN_SIMILARITY;

  const params: unknown[] = [
    ...filterParams,
    vecLiteral,          // $vIdx
    q,                   // $qIdx (ts_headline + kw_hit tsquery)
    CANDIDATE_POOL,      // $poolIdx
    politicianLimit,     // $plIdx
    politicianOffset,    // $poIdx
    per_group_limit,     // $pglIdx
    MAX_DISTANCE,        // $mdIdx
    sort,                // $sortIdx
  ];
  const base = filterParams.length;
  const vIdx = base + 1;
  const qIdx = base + 2;
  const poolIdx = base + 3;
  const plIdx = base + 4;
  const poIdx = base + 5;
  const pglIdx = base + 6;
  const mdIdx = base + 7;
  const sortIdx = base + 8;

  const sql = `
    WITH candidates AS (
      SELECT sc.id AS chunk_id, sc.speech_id, sc.chunk_index, sc.text, sc.tsv,
             sc.spoken_at, sc.language, sc.level, sc.province_territory,
             sc.party_at_time, sc.politician_id, sc.session_id, sc.tsv_config,
             (sc.embedding <=> $${vIdx}::vector)::float AS distance,
             (sc.tsv @@ websearch_to_tsquery(COALESCE(sc.tsv_config, 'simple')::regconfig, $${qIdx}))::int AS kw_hit
        FROM speech_chunks sc
       WHERE ${whereSql}
       ORDER BY sc.embedding <=> $${vIdx}::vector
       LIMIT $${poolIdx}
    ),
    qualified AS (
      SELECT * FROM candidates
       WHERE politician_id IS NOT NULL
         AND distance <= $${mdIdx}
    ),
    pol_stats AS (
      SELECT politician_id,
             COUNT(*)::int         AS mention_count,
             MIN(distance)::float  AS best_dist,
             AVG(distance)::float  AS avg_dist,
             SUM(kw_hit)::int      AS keyword_hits
        FROM qualified
       GROUP BY politician_id
    ),
    top_pols AS (
      SELECT * FROM pol_stats
       ORDER BY
         CASE WHEN $${sortIdx} = 'mentions'     THEN -mention_count END ASC,
         CASE WHEN $${sortIdx} = 'keyword_hits' THEN -keyword_hits  END ASC,
         CASE WHEN $${sortIdx} = 'avg_match'    THEN  avg_dist      END ASC,
         CASE WHEN $${sortIdx} = 'best_match'   THEN  best_dist     END ASC,
         best_dist
       LIMIT $${plIdx} OFFSET $${poIdx}
    ),
    ranked AS (
      SELECT q.*, tp.best_dist, tp.avg_dist, tp.mention_count, tp.keyword_hits,
             ROW_NUMBER() OVER (PARTITION BY q.politician_id ORDER BY q.distance) AS rn_in_pol
        FROM qualified q
        JOIN top_pols tp ON tp.politician_id = q.politician_id
    )
    SELECT r.chunk_id, r.speech_id, r.chunk_index, r.text,
           ts_headline(
             COALESCE(r.tsv_config, 'simple')::regconfig,
             r.text,
             websearch_to_tsquery(COALESCE(r.tsv_config, 'simple')::regconfig, $${qIdx}),
             'MaxWords=35, MinWords=15, ShortWord=3, MaxFragments=2, FragmentDelimiter=" … ", HighlightAll=FALSE'
           ) AS snippet_html,
           r.distance, r.spoken_at, r.language, r.level, r.province_territory,
           r.party_at_time, r.politician_id,
           r.best_dist, r.avg_dist, r.mention_count, r.keyword_hits,
           r.rn_in_pol,
           p.name                        AS politician_name,
           p.openparliament_slug         AS politician_slug,
           p.photo_url                   AS politician_photo_url,
           p.photo_path                  AS politician_photo_path,
           p.party                       AS politician_party,
           socials.items                 AS politician_socials,
           s.speaker_name_raw            AS speech_speaker_name_raw,
           s.speaker_role                AS speech_speaker_role,
           s.source_url                  AS speech_source_url,
           s.source_anchor               AS speech_source_anchor,
           ls.parliament_number,
           ls.session_number
      FROM ranked r
      LEFT JOIN politicians p           ON p.id  = r.politician_id
      LEFT JOIN speeches s              ON s.id  = r.speech_id
      LEFT JOIN legislative_sessions ls ON ls.id = s.session_id
      LEFT JOIN LATERAL (
        SELECT jsonb_agg(
                 jsonb_build_object('platform', ps.platform, 'url', ps.url, 'handle', ps.handle)
                 ORDER BY ps.platform
               ) AS items
          FROM politician_socials ps
         WHERE ps.politician_id = p.id
           AND COALESCE(ps.is_live, true)
      ) socials ON true
     WHERE r.rn_in_pol <= $${pglIdx}
     ORDER BY
       CASE WHEN $${sortIdx} = 'mentions'     THEN -r.mention_count END ASC,
       CASE WHEN $${sortIdx} = 'keyword_hits' THEN -r.keyword_hits  END ASC,
       CASE WHEN $${sortIdx} = 'avg_match'    THEN  r.avg_dist      END ASC,
       CASE WHEN $${sortIdx} = 'best_match'   THEN  r.best_dist     END ASC,
       r.best_dist, r.politician_id, r.spoken_at ASC NULLS FIRST
  `;

  // HNSW: ef_search must be ≥ the LIMIT for the index to actually return
  // that many rows; 1000 is the pgvector 0.8.2 maximum. SET LOCAL inside
  // a transaction scopes it so pooled connections don't carry the bump
  // elsewhere.
  const client = await pool.connect();
  let rows: SpeechSearchRow[] = [];
  try {
    await client.query("BEGIN");
    await client.query("SET LOCAL hnsw.ef_search = 1000");
    const res = await client.query(sql, params as unknown as unknown[]);
    rows = res.rows as SpeechSearchRow[];
    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    app.log.error({ err, q, sort }, "grouped search failed");
    throw err;
  } finally {
    client.release();
  }

  // Walk the pre-sorted rows (best_dist, politician_id, spoken_at) and
  // bucket consecutive same-politician rows into one group.
  interface ChunkItem {
    chunk_id: string;
    speech_id: string;
    chunk_index: number;
    text: string;
    snippet_html: string | null;
    similarity: number | null;
    spoken_at: string | null;
    language: "en" | "fr";
    level: string | null;
    province_territory: string | null;
    party_at_time: string | null;
    speech: {
      speaker_name_raw: string;
      speaker_role: string | null;
      source_url: string | null;
      source_anchor: string | null;
      session: { parliament_number: number; session_number: number } | null;
    };
  }
  interface PoliticianGroup {
    politician: {
      id: string;
      name: string | null;
      slug: string | null;
      photo_url: string | null;
      party: string | null;
      socials: Array<{ platform: string; url: string; handle: string | null }>;
    };
    best_similarity: number | null;
    avg_similarity: number | null;
    mention_count: number;
    keyword_hits: number;
    chunks: ChunkItem[];
  }

  const groups: PoliticianGroup[] = [];
  let current: PoliticianGroup | null = null;
  for (const r of rows) {
    if (!r.politician_id) continue;
    if (!current || current.politician.id !== r.politician_id) {
      current = {
        politician: {
          id: r.politician_id,
          name: r.politician_name,
          slug: r.politician_slug,
          photo_url: resolvePhotoUrl({
            photo_path: r.politician_photo_path,
            photo_url: r.politician_photo_url,
          }),
          party: r.politician_party,
          socials: r.politician_socials ?? [],
        },
        best_similarity: r.best_dist != null ? 1 - r.best_dist : null,
        avg_similarity: r.avg_dist != null ? 1 - r.avg_dist : null,
        mention_count: r.mention_count ?? 0,
        keyword_hits: r.keyword_hits ?? 0,
        chunks: [],
      };
      groups.push(current);
    }
    current.chunks.push({
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
      speech: {
        speaker_name_raw: r.speech_speaker_name_raw,
        speaker_role: r.speech_speaker_role,
        source_url: r.speech_source_url,
        source_anchor: r.speech_source_anchor,
        session:
          r.parliament_number !== null && r.session_number !== null
            ? { parliament_number: r.parliament_number, session_number: r.session_number }
            : null,
      },
    });
  }

  return {
    mode: "grouped",
    group_by: "politician" as const,
    page,
    limit: politicianLimit,
    per_group_limit,
    groups,
    total_politicians: groups.length,
  };
}

/** Timeline-mode search: flat list of chunks, ranked by semantic distance
 *  when `q` is present, else by recency. Used by both the public
 *  /speeches route and the gated /politician-quotes deep-dive route, so
 *  the SQL lives once and both callers share the same response shape.
 *
 *  options.minSimilarity (0..1, requires q) — drop chunks whose cosine
 *  similarity to the query falls below this floor from BOTH the count
 *  and the result set. /politician-quotes passes 0.45, mirroring
 *  handleGroupedByPolitician's MIN_SIMILARITY, so the deep-dive's count
 *  matches the headline `mention_count` on the same card and doesn't
 *  inflate to "every chunk this politician has ever uttered under the
 *  parent search's structural filters". /speeches doesn't pass it, so
 *  the public timeline keeps its existing wide-net behaviour. */
async function runTimelineSearch(
  input: SearchInput,
  options: { minSimilarity?: number } = {},
) {
  const { q, page, limit } = input;
  const { minSimilarity } = options;
  const offset = (page - 1) * limit;

  const { whereSql, filterParams } = buildFilterWhere(input);

  // Encode the query once; both the threshold (when set) and the rank
  // ORDER BY share the same vector literal.
  let queryVecLiteral: string | null = null;
  if (q) {
    const vec = await encodeQuery(q);
    queryVecLiteral = toPgVector(vec);
  }
  const applyThreshold =
    !!q && queryVecLiteral !== null && minSimilarity != null && minSimilarity > 0;
  const maxDistance = applyThreshold ? 1 - (minSimilarity as number) : null;

  // Cap the count at 1000 to avoid a full HNSW/filter scan just to
  // compute "pages". UIs beyond page 50 (at limit=20) are unusable
  // anyway; keyset pagination is a later concern.
  const COUNT_CAP = 1000;
  const countParams: (string | number | string[])[] = [...filterParams];
  let countWhere = whereSql;
  if (applyThreshold) {
    countParams.push(queryVecLiteral as string);
    const cvIdx = countParams.length;
    countParams.push(maxDistance as number);
    const cdIdx = countParams.length;
    countWhere = `${whereSql} AND (sc.embedding <=> $${cvIdx}::vector) <= $${cdIdx}`;
  }
  const countRow = await queryOne<{ n: number }>(
    `SELECT COUNT(*)::int AS n FROM (
       SELECT 1 FROM speech_chunks sc
       WHERE ${countWhere}
       LIMIT ${COUNT_CAP + 1}
     ) x`,
    countParams,
  );
  const totalRaw = countRow?.n ?? 0;
  const totalCapped = totalRaw > COUNT_CAP;
  const total = totalCapped ? COUNT_CAP : totalRaw;

  // Build the main SELECT.
  const params: (string | number | string[])[] = [...filterParams];
  let orderBy: string;
  let vectorParamIndex: number | null = null;
  if (q && queryVecLiteral !== null) {
    params.push(queryVecLiteral);
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

  let mainWhere = whereSql;
  if (applyThreshold) {
    // Reuse vectorParamIndex (already pushed above) and add a fresh
    // distance param so this WHERE doesn't share params with the count
    // query's separate paramslist.
    params.push(maxDistance as number);
    const mdIdx = params.length;
    mainWhere = `${whereSql} AND (sc.embedding <=> $${vectorParamIndex}::vector) <= $${mdIdx}`;
  }

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
      s.speaker_role                AS speech_speaker_role,
      s.source_url                  AS speech_source_url,
      s.source_anchor               AS speech_source_anchor,
      ls.parliament_number,
      ls.session_number
    FROM speech_chunks sc
    LEFT JOIN politicians p           ON p.id  = sc.politician_id
    LEFT JOIN speeches   s            ON s.id  = sc.speech_id
    LEFT JOIN legislative_sessions ls ON ls.id = s.session_id
    LEFT JOIN LATERAL (
      SELECT jsonb_agg(
               jsonb_build_object('platform', ps.platform, 'url', ps.url, 'handle', ps.handle)
               ORDER BY ps.platform
             ) AS items
        FROM politician_socials ps
       WHERE ps.politician_id = p.id
         AND COALESCE(ps.is_live, true)
    ) socials ON true
    WHERE ${mainWhere}
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
      speaker_role: r.speech_speaker_role,
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
    mode: (q ? "semantic" : "recent") as "semantic" | "recent",
  };
}

export default async function searchRoutes(app: FastifyInstance) {
  app.get("/speeches", async (req, reply) => {
    const parsed = searchQuery.safeParse(req.query);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const { group_by } = parsed.data;

    if (group_by === "politician") {
      return handleGroupedByPolitician(app, reply, parsed.data);
    }

    if (!parsed.data.q && !hasAnyStructuralFilter(parsed.data)) {
      return reply.badRequest("provide `q` or at least one filter (politician_ids, party, level, province, from, to)");
    }

    return runTimelineSearch(parsed.data);
  });

  // Authenticated deep-dive: every quote one politician has on the query.
  // Backs the "Show all N matching quotes" expand affordance on /search's
  // politician view. Hard-gated behind requireUser + a per-user rate limit
  // so anon callers can't bypass the "sign in to expand" UI by URL — same
  // posture as POST /reports (the established gated-search-feature
  // precedent in this codebase).
  app.get(
    "/politician-quotes",
    {
      preHandler: [requireUser],
      config: {
        rateLimit: {
          max: 60,
          timeWindow: "1 minute",
          keyGenerator: (req) => `expand-quotes:${getUser(req)?.sub ?? req.ip}`,
        },
      },
    },
    async (req, reply) => {
      const parsed = expandQuery.safeParse(req.query);
      if (!parsed.success) return reply.badRequest(parsed.error.message);
      if (!parsed.data.q) {
        return reply.badRequest("`q` is required for /politician-quotes");
      }
      // Force timeline mode + collapse to the single requested politician.
      // per_group_limit/sort/group_by don't apply here but SearchInput
      // demands them; supply the schema defaults so runTimelineSearch's
      // shared filter builder works unchanged.
      const input: SearchInput = {
        ...parsed.data,
        politician_id: undefined,
        politician_ids: [parsed.data.politician_id],
        group_by: "timeline",
        per_group_limit: 5,
        sort: "mentions",
      };
      // 0.45 mirrors handleGroupedByPolitician's MIN_SIMILARITY so the
      // deep-dive count matches mention_count on the same card —
      // "actually matching quotes for this query", not "every chunk
      // this MP has ever uttered under the structural filters". Client
      // can tighten further (e.g. 0.7 for "strong matches only") but
      // never loosen below the 0.45 floor.
      const clientMin = parsed.data.min_similarity ?? 0;
      const effectiveMin = Math.max(0.45, clientMin);
      return runTimelineSearch(input, { minSimilarity: effectiveMin });
    }
  );

  app.get("/facets", async (req, reply) => {
    const parsed = baseFilterSchema.safeParse(req.query);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const { q } = parsed.data;

    if (!q && !hasAnyStructuralFilter(parsed.data)) {
      return reply.badRequest("provide `q` or at least one filter to aggregate");
    }

    const { whereSql, filterParams } = buildFilterWhere(parsed.data);
    const params: (string | number | string[])[] = [...filterParams];

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
