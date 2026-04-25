import sanitizeHtml from "sanitize-html";
import { config } from "../config.js";
import { pool, queryOne } from "../db.js";
import { encodeQuery, toPgVector } from "../routes/search.js";
import { callJsonObjectModel } from "./openrouter.js";

/**
 * Premium-reports helpers.
 *
 * Two responsibilities live here:
 *
 * 1. Cost estimation. Reused by:
 *      - POST /reports/estimate (user-facing modal)
 *      - POST /reports         (server-side re-check before placing the hold)
 *      - reports-worker        (Python) — port-of-prompt logic mirrors this,
 *                                          but the Python side reads
 *                                          REPORT_BUCKET_SIZE/MAX_CHUNKS from
 *                                          its own env.
 *
 * 2. Map-reduce orchestration. The api can run map-reduce inline when
 *    the operator wants synchronous reports (small jobs, debugging),
 *    but the production path is the worker — which writes the same
 *    SQL and a Python-port of the same prompts. Keep the prompts
 *    char-for-char identical across TS and Python: model behaviour
 *    must be a function of the prompt, not the entry point.
 *
 * The runMapReduce() function below is wired up but UNUSED in phase 1b
 * — the worker is the production caller. It exists for two reasons:
 * (a) future debugging / admin-rerun-of-failed-job paths, (b) to keep
 * the prompt logic single-source-of-truth in the api codebase so the
 * Python worker can be reviewed against it.
 */

// ────────────────────────────────────────────────────────────
// Cost
// ────────────────────────────────────────────────────────────

export interface ReportCostEstimate {
  estimated_chunks: number;
  candidate_chunks: number;
  estimated_credits: number;
  capped: boolean;
}

/**
 * Compute the credit cost for a (politician, query) pair without
 * placing a hold. Embeds the query via TEI, runs an HNSW count of
 * candidates, applies the REPORT_MAX_CHUNKS cap, and computes the
 * formula. Read-only.
 */
export async function estimateReportCost(args: {
  politicianId: string;
  query: string;
}): Promise<ReportCostEstimate> {
  const vec = await encodeQuery(args.query);
  const vecLiteral = toPgVector(vec);

  // The `candidate_chunks` value is the number of chunks the worker
  // will actually pass to map-reduce — capped at REPORT_MAX_CHUNKS.
  // We compute it inline rather than fetching all candidates because
  // counting up to the cap is enough information to price the report.
  // The 0.55 distance threshold matches the grouped-search MAX_DISTANCE
  // (1 - MIN_SIMILARITY=0.45 in routes/search.ts) so the candidate
  // pool is shaped the same way.
  const MAX_DISTANCE = 0.55;
  const cap = config.reports.maxChunks;

  const client = await pool.connect();
  let candidateCount: number;
  try {
    await client.query("BEGIN");
    await client.query(`SET LOCAL hnsw.ef_search = ${config.reports.hnswEfSearch}`);
    const res = await client.query<{ n: string }>(
      `WITH cand AS (
         SELECT sc.id
           FROM speech_chunks sc
          WHERE sc.embedding IS NOT NULL
            AND sc.politician_id = $1
            AND (sc.embedding <=> $2::vector) <= $3
          ORDER BY sc.embedding <=> $2::vector
          LIMIT $4
       )
       SELECT count(*)::text AS n FROM cand`,
      [args.politicianId, vecLiteral, MAX_DISTANCE, cap]
    );
    await client.query("COMMIT");
    candidateCount = Number(res.rows[0]?.n ?? 0);
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }

  const usedChunks = Math.min(candidateCount, cap);
  const buckets = Math.ceil(usedChunks / config.reports.bucketSize);
  const estimated_credits =
    config.reports.baseCostCredits + buckets * config.reports.perChunkBucketCost;

  return {
    estimated_chunks: usedChunks,
    candidate_chunks: candidateCount,
    estimated_credits,
    capped: candidateCount > cap,
  };
}

// ────────────────────────────────────────────────────────────
// Chunk selection
// ────────────────────────────────────────────────────────────

export interface ReportChunk {
  id: string;
  speech_id: string;
  text: string;
  spoken_at: Date | null;
  party_at_time: string | null;
  parliament_number: number | null;
  session_number: number | null;
  source_url: string | null;
  source_anchor: string | null;
}

export async function selectReportChunks(args: {
  politicianId: string;
  queryEmbedding: number[];
  limit: number;
}): Promise<ReportChunk[]> {
  const vecLiteral = toPgVector(args.queryEmbedding);
  const MAX_DISTANCE = 0.55;
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query(`SET LOCAL hnsw.ef_search = ${config.reports.hnswEfSearch}`);
    const res = await client.query<ReportChunk>(
      `SELECT sc.id, sc.speech_id, sc.text, sc.spoken_at, sc.party_at_time,
              ls.parliament_number, ls.session_number,
              s.source_url, s.source_anchor
         FROM speech_chunks sc
         JOIN speeches s ON s.id = sc.speech_id
         LEFT JOIN legislative_sessions ls ON ls.id = s.session_id
        WHERE sc.embedding IS NOT NULL
          AND sc.politician_id = $1
          AND (sc.embedding <=> $2::vector) <= $3
        ORDER BY sc.embedding <=> $2::vector
        LIMIT $4`,
      [args.politicianId, vecLiteral, MAX_DISTANCE, args.limit]
    );
    await client.query("COMMIT");
    return res.rows;
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

// ────────────────────────────────────────────────────────────
// Bucketing + prompts (kept char-for-char in sync with the Python port)
// ────────────────────────────────────────────────────────────

export function bucketChunks<T>(chunks: T[], bucketSize: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < chunks.length; i += bucketSize) {
    out.push(chunks.slice(i, i + bucketSize));
  }
  return out;
}

export const SYSTEM_PROMPT_MAP = `You are a careful research analyst. You will be shown N quotes from a single Canadian politician on a specific topic. Extract the politician's positions and themes from these quotes. Output strictly valid JSON of this exact shape:

{
  "themes": [
    {
      "label": "<short noun-phrase label, < 60 chars>",
      "positions": [
        {
          "summary": "<one neutral sentence describing the politician's stated position>",
          "chunk_ids": ["<chunk_id from input, copied verbatim>", ...]
        }
      ]
    }
  ]
}

Rules:
- "chunk_ids" MUST be copied verbatim from the input. Never invent IDs.
- Every position must reference at least one input chunk_id.
- "summary" must be neutral and observational — do not editorialise, do not draw conclusions, do not call statements right or wrong.
- If a quote is the politician quoting an opponent ("the member opposite said…"), treat it as rhetorical framing, not their own position. Do not include such quotes as positions.
- Some quotes may be only tangentially related to the query topic — the retrieval system errs on the side of recall, so a few off-topic chunks may slip in. Omit any chunk where the politician is not actually speaking about the topic in a substantive way. Producing fewer, well-evidenced themes is preferred over many themes built on weak evidence.
- If multiple quotes express the same position, group them under one "positions" entry with multiple chunk_ids.
- Themes should be granular but not redundant: prefer 2-5 themes per bucket.`;

export const SYSTEM_PROMPT_REDUCE = `You are synthesising the work of multiple analysts who each read a subset of a politician's quotes on a topic. You will be shown each analyst's themes and positions in JSON form. Produce a single coherent HTML report.

Output strictly valid JSON of this exact shape:

{
  "summary": "<one paragraph (60-120 words) framing what the politician's record shows on this topic, in neutral observational tone>",
  "html": "<HTML body, see allowed tags below>"
}

Allowed HTML tags ONLY: <p>, <h2>, <h3>, <ul>, <ol>, <li>, <blockquote>, <em>, <strong>, <a href="…">. Any other tag will be stripped server-side.

Rules:
- Structure the HTML with <h2> sections per theme; under each theme, group positions and reference quotes inline.
- Every claim that asserts a position MUST link to at least one source quote. Format the link as <a href="CHUNK:<chunk_id>">…</a> using the literal token CHUNK: followed by a chunk_id from the input. The system will rewrite these to real anchored URLs after you respond. Never output a real URL — only the CHUNK:<id> token form.
- Preserve the chunk_ids verbatim from the input analyst output. Never invent IDs.
- Neutral observational tone throughout. Frame as "the politician has said X (link)", never as "the politician is wrong about X" or "the politician contradicts themselves on X".
- If the analyst output includes contradictory positions across time, describe them descriptively — "in <year> they said X (link); in <later year> they said Y (link)" — without using the word "contradiction".
- The summary paragraph is the FIRST thing the user reads. Make it factual and substantive; avoid filler like "this report covers…".
- Do not include a top-level <h1> — the page chrome supplies the title. Start with a <p> or <h2>.`;

function ordinalSuffix(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return "th";
  switch (n % 10) {
    case 1:
      return "st";
    case 2:
      return "nd";
    case 3:
      return "rd";
    default:
      return "th";
  }
}

function formatDate(d: Date | null): string {
  if (!d) return "unknown";
  return d.toISOString().slice(0, 10);
}

export function buildMapPrompt(args: {
  politicianName: string;
  party: string | null;
  topic: string;
  chunks: ReportChunk[];
}): string {
  const lines: string[] = [];
  const partyFragment = args.party ? ` (${args.party})` : "";
  lines.push(`Politician: ${args.politicianName}${partyFragment}`);
  lines.push(`Query topic: ${args.topic}`);
  lines.push("");
  for (const c of args.chunks) {
    const truncated = c.text.length > 1200 ? `${c.text.slice(0, 1200)}…[truncated]` : c.text;
    lines.push(`Quote (chunk_id=${c.id}):`);
    lines.push(`  Date: ${formatDate(c.spoken_at)}`);
    if (c.parliament_number !== null && c.session_number !== null) {
      lines.push(
        `  Parliament: ${c.parliament_number}${ordinalSuffix(c.parliament_number)}, Session ${c.session_number}`
      );
    }
    if (c.party_at_time) lines.push(`  Party at time: ${c.party_at_time}`);
    lines.push(`  Text: ${truncated}`);
    lines.push("");
  }
  lines.push("Return the JSON object described in the system prompt.");
  return lines.join("\n");
}

export function buildReducePrompt(args: {
  politicianName: string;
  party: string | null;
  topic: string;
  bucketSummaries: unknown[];
}): string {
  return [
    `Politician: ${args.politicianName}${args.party ? ` (${args.party})` : ""}`,
    `Query topic: ${args.topic}`,
    "",
    "Per-bucket analyst output (JSON array, each element is one analyst's themes):",
    JSON.stringify(args.bucketSummaries, null, 2),
    "",
    "Return the synthesised JSON object described in the system prompt.",
  ].join("\n");
}

// ────────────────────────────────────────────────────────────
// HTML sanitisation + chunk-link rewriting
// ────────────────────────────────────────────────────────────

const SANITIZE_OPTIONS: sanitizeHtml.IOptions = {
  allowedTags: ["p", "h2", "h3", "ul", "ol", "li", "blockquote", "em", "strong", "a"],
  allowedAttributes: { a: ["href"] },
  // Internal links only. The reduce prompt instructs the model to emit
  // CHUNK:<id> tokens which we rewrite below; anything else surviving
  // sanitisation must be a valid /speeches/... path. URL schemes like
  // javascript:, data:, vbscript: are denied by the schemes list.
  allowedSchemes: [],
  allowedSchemesByTag: { a: ["http", "https"] },
  allowProtocolRelative: false,
  transformTags: {
    a: (tagName, attribs) => {
      const href = attribs.href ?? "";
      if (href.startsWith("/speeches/")) return { tagName, attribs };
      // Strip the href on anything else; the link still renders text.
      return { tagName, attribs: {} };
    },
  },
};

/**
 * Replace every `CHUNK:<chunk_id>` href with a real /speeches/<speech_id>#chunk-<chunk_id>
 * URL using the chunk metadata captured at fetch time. Unknown chunk_ids
 * (model hallucination) get the href stripped — the link text remains so
 * the report doesn't look broken, but the reader can't navigate to a
 * fabricated source.
 *
 * Run BEFORE sanitisation so the post-sanitise output uses real internal
 * paths and survives the protocol allowlist.
 */
export function rewriteChunkLinks(html: string, chunks: ReportChunk[]): string {
  const byId = new Map<string, ReportChunk>();
  for (const c of chunks) byId.set(c.id, c);
  return html.replace(
    /href=(["'])CHUNK:([0-9a-f-]{36})\1/gi,
    (_match, quote: string, id: string) => {
      const c = byId.get(id);
      if (!c) return ""; // strip the href entirely
      return `href=${quote}/speeches/${c.speech_id}#chunk-${id}${quote}`;
    }
  );
}

export function sanitizeReportHtml(html: string): string {
  return sanitizeHtml(html, SANITIZE_OPTIONS);
}

// ────────────────────────────────────────────────────────────
// Map-reduce (provided for completeness; production path is the worker)
// ────────────────────────────────────────────────────────────

export interface MapReduceOutput {
  html: string;
  summary: string;
  model_used: string;
  tokens_in: number;
  tokens_out: number;
}

export async function runMapReduce(args: {
  politicianName: string;
  party: string | null;
  topic: string;
  chunks: ReportChunk[];
}): Promise<MapReduceOutput> {
  const buckets = bucketChunks(args.chunks, config.reports.bucketSize);
  const model = config.reports.model;
  let tokensIn = 0;
  let tokensOut = 0;

  // Bucket map calls — concurrency 2 to be polite to provider burst
  // limits without serialising big jobs end-to-end.
  const bucketSummaries: unknown[] = [];
  for (let i = 0; i < buckets.length; i += 2) {
    const slice = buckets.slice(i, i + 2);
    const results = await Promise.all(
      slice.map((bucket) =>
        callJsonObjectModel({
          model,
          messages: [
            { role: "system", content: SYSTEM_PROMPT_MAP },
            {
              role: "user",
              content: buildMapPrompt({
                politicianName: args.politicianName,
                party: args.party,
                topic: args.topic,
                chunks: bucket,
              }),
            },
          ],
          timeoutMs: config.reports.timeoutMs,
        })
      )
    );
    for (const r of results) {
      if (!r.ok) throw new Error(`map call failed: ${r.error.kind}`);
      tokensIn += r.value.tokensIn ?? 0;
      tokensOut += r.value.tokensOut ?? 0;
      bucketSummaries.push(JSON.parse(r.value.content));
    }
  }

  const reduce = await callJsonObjectModel({
    model,
    messages: [
      { role: "system", content: SYSTEM_PROMPT_REDUCE },
      {
        role: "user",
        content: buildReducePrompt({
          politicianName: args.politicianName,
          party: args.party,
          topic: args.topic,
          bucketSummaries,
        }),
      },
    ],
    timeoutMs: config.reports.timeoutMs,
  });
  if (!reduce.ok) throw new Error(`reduce call failed: ${reduce.error.kind}`);
  tokensIn += reduce.value.tokensIn ?? 0;
  tokensOut += reduce.value.tokensOut ?? 0;

  const parsed = JSON.parse(reduce.value.content) as { html?: string; summary?: string };
  if (typeof parsed.html !== "string" || typeof parsed.summary !== "string") {
    throw new Error("reduce output missing html/summary");
  }
  const rewritten = rewriteChunkLinks(parsed.html, args.chunks);
  const sanitised = sanitizeReportHtml(rewritten);

  return {
    html: sanitised,
    summary: parsed.summary,
    model_used: reduce.value.model,
    tokens_in: tokensIn,
    tokens_out: tokensOut,
  };
}

// ────────────────────────────────────────────────────────────
// Misc: politician metadata for prompt + viewer header
// ────────────────────────────────────────────────────────────

export interface ReportPoliticianHeader {
  id: string;
  name: string;
  party: string | null;
  photo_url: string | null;
}

export async function getPoliticianHeader(
  politicianId: string
): Promise<ReportPoliticianHeader | null> {
  const row = await queryOne<ReportPoliticianHeader>(
    `SELECT id, name, party, photo_url
       FROM politicians
      WHERE id = $1`,
    [politicianId]
  );
  return row ?? null;
}

/**
 * Tier-aware daily report cap. unlimited = no cap; suspended is enforced
 * earlier by requireUser. Default vs extended are env-driven.
 */
export function dailyReportCapForTier(tier: string): number | null {
  if (tier === "unlimited") return null;
  if (tier === "extended") return config.reports.rateLimitExtendedPerDay;
  return config.reports.rateLimitDefaultPerDay;
}

/** Count caller's report_jobs in the trailing 24h, excluding cancelled/refunded. */
export async function countRecentReportJobs(userId: string): Promise<number> {
  const row = await queryOne<{ n: string }>(
    `SELECT count(*)::text AS n
       FROM report_jobs
      WHERE user_id = $1
        AND created_at > now() - interval '24 hours'
        AND status NOT IN ('cancelled','refunded')`,
    [userId]
  );
  return Number(row?.n ?? 0);
}

