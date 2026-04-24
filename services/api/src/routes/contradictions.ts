import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { config } from "../config.js";
import { query, queryOne } from "../db.js";
import { requireUser } from "../middleware/user-auth.js";
import { requireCsrf } from "../lib/csrf.js";

/**
 * AI contradictions analysis for the grouped search view.
 *
 * Two endpoints:
 *   GET  /api/v1/contradictions/meta     — public, reports feature status
 *                                            + configured model so the
 *                                            frontend can grey the button
 *                                            and show the model name in
 *                                            the consent modal.
 *   POST /api/v1/contradictions/analyze  — requireUser + requireCsrf.
 *                                            Loads the caller's chunks,
 *                                            validates ownership, calls
 *                                            OpenRouter, returns flagged
 *                                            pairs + rationales.
 *
 * Why POST is gated on requireUser: the OpenRouter free-tier quota is a
 * shared resource. Signed-in users give us per-user attribution plus the
 * natural rate-limiting of the magic-link flow. Anonymous users see the
 * button greyed with a "Sign in to analyze" tooltip.
 *
 * The endpoint deliberately re-SELECTs chunk text + session metadata
 * rather than trusting anything from the client body besides the UUIDs.
 * This means a caller cannot smuggle altered quotes into the model.
 */

const analyzeBody = z.object({
  politician_id: z.string().uuid(),
  query: z.string().trim().min(1).max(500),
  chunk_ids: z.array(z.string().uuid()).min(2).max(10),
});

interface ChunkRow {
  id: string;
  text: string;
  // pg's default type parsers return Date for timestamp/timestamptz/date
  // columns. Early versions of this route typed it as string and broke on
  // Date.localeCompare — keep this annotation honest.
  spoken_at: Date | null;
  party_at_time: string | null;
  parliament_number: number | null;
  session_number: number | null;
}

interface PoliticianRow {
  name: string | null;
  party: string | null;
}

const PAIR_KIND = ["contradiction", "evolution", "consistent"] as const;

const modelOutputSchema = z.object({
  pairs: z
    .array(
      z.object({
        a_chunk_id: z.string(),
        b_chunk_id: z.string(),
        kind: z.enum(PAIR_KIND),
        rationale: z.string().trim().min(1).max(600),
      })
    )
    .max(30),
  summary: z.string().trim().max(500).optional(),
});

const SYSTEM_PROMPT = `You analyze quotes from a single Canadian politician on a specific topic and identify pairs that contradict, evolve, or are consistent.

Output strictly valid JSON with exactly this shape. Every field name must match exactly — no renaming, no empty keys, no extra fields:

{
  "pairs": [
    {
      "a_chunk_id": "<the exact chunk_id string from the input, copied verbatim>",
      "b_chunk_id": "<a different exact chunk_id string from the input, copied verbatim>",
      "kind": "contradiction",
      "rationale": "one sentence under 300 characters"
    }
  ],
  "summary": "optional one-sentence overall take"
}

Required object keys in every pair: "a_chunk_id", "b_chunk_id", "kind", "rationale". Never use an empty string as a key.

The chunk_id values MUST be copied verbatim from the input quotes. Do not invent UUIDs. Do not partially quote them. If you cannot reference a real chunk_id from the input, omit that pair.

Allowed values for "kind":
- "contradiction" — the two statements make directly opposite claims about the same specific issue.
- "evolution" — the politician softened or hardened their stance over time without a clean reversal. A stance shift after a party change is "evolution", not a personal contradiction.
- "consistent" — the two statements are compatible, restate the same position, or the model explicitly asserts no contradiction.

Calibration rules:
- Prefer "evolution" or "consistent" over "contradiction" when in doubt.
- If a quote appears to be the politician quoting an opponent ("The member opposite said…"), treat it as rhetorical framing rather than the politician's own position.
- Return at most one entry per unordered chunk pair.
- If no contradictions are found, include at least one "consistent" pair referencing two real input chunk_ids so the UI has something to show.
- Rationale must be a single neutral sentence framed as an observation, never as a verdict. Do not include quoted text.`;

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
  // ISO YYYY-MM-DD is enough precision for legislative context and plays
  // nicer in the prompt than the default Date.toString() blob.
  return d.toISOString().slice(0, 10);
}

function buildUserPrompt(politician: PoliticianRow, topic: string, chunks: ChunkRow[]): string {
  const lines: string[] = [];
  const partyFragment = politician.party ? ` (${politician.party})` : "";
  lines.push(`Politician: ${politician.name ?? "unknown"}${partyFragment}`);
  lines.push(`Query topic: ${topic}`);
  lines.push("");
  for (const c of chunks) {
    const truncated = c.text.length > 800 ? `${c.text.slice(0, 800)}…[truncated]` : c.text;
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
  lines.push("Return only the JSON object. Reference chunk_ids exactly as shown above.");
  return lines.join("\n");
}

interface OpenRouterChoice {
  message?: { content?: string };
}
interface OpenRouterResponse {
  choices?: OpenRouterChoice[];
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Free-tier models occasionally mangle structured output: renaming a
 * key to "" (empty string), swapping key order, or wrapping pairs in
 * an extra object. Before handing the parsed JSON to zod, try a small
 * set of deterministic repairs on each pair. Only repairs that are
 * unambiguous (e.g. the record has exactly two UUID-shaped string
 * values and a `kind` + `rationale`) are applied.
 *
 * If the repair can't produce a valid pair, the original object is
 * returned unchanged and zod validation will reject it — so we never
 * silently fabricate shape we don't have evidence for.
 */
function repairModelOutput(input: unknown): unknown {
  if (!input || typeof input !== "object") return input;
  const root = input as Record<string, unknown>;
  const pairs = root.pairs;
  if (!Array.isArray(pairs)) return input;

  const repaired = pairs.map((p) => {
    if (!p || typeof p !== "object") return p;
    const obj = p as Record<string, unknown>;

    // Already well-formed — leave alone.
    if (
      typeof obj.a_chunk_id === "string" &&
      typeof obj.b_chunk_id === "string"
    ) {
      return obj;
    }

    // Collect every string value that is UUID-shaped, ignoring the key
    // name. If we end up with exactly two, we can confidently assign
    // them to a_chunk_id and b_chunk_id in their original insertion
    // order.
    const uuids: string[] = [];
    for (const v of Object.values(obj)) {
      if (typeof v === "string" && UUID_RE.test(v)) uuids.push(v);
    }
    if (uuids.length !== 2) return obj;

    return {
      ...obj,
      a_chunk_id: uuids[0],
      b_chunk_id: uuids[1],
    };
  });

  return { ...root, pairs: repaired };
}

export default async function contradictionsRoutes(app: FastifyInstance) {
  // ── GET /meta ────────────────────────────────────────────────
  app.get("/meta", async (_req, reply) => {
    return reply.send({
      enabled: config.openrouter.enabled,
      model: config.openrouter.enabled ? config.openrouter.model : null,
      provider: "openrouter",
    });
  });

  // ── POST /analyze ────────────────────────────────────────────
  app.post("/analyze", { preHandler: [requireUser, requireCsrf] }, async (req, reply) => {
    if (!config.openrouter.enabled) {
      return reply.code(503).send({ error: "AI contradictions analysis not configured" });
    }

    const parsed = analyzeBody.safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid body", details: parsed.error.flatten() });
    }
    const { politician_id, query: topic } = parsed.data;

    // Dedupe chunk_ids: duplicates from the client would cause the
    // ownership length-check to fail even when every unique id is
    // valid. Coalescing early is more forgiving and doesn't cost
    // anything — the model gets unique quotes regardless.
    const chunkIds = [...new Set(parsed.data.chunk_ids)];
    if (chunkIds.length < 2) {
      return reply.code(400).send({ error: "need at least 2 distinct chunk_ids" });
    }

    // Chunk-ownership + metadata fetch in one round-trip. Join session
    // through speeches — speech_chunks.session_id is known to diverge
    // from speeches.session_id on every federal row. See CLAUDE.md.
    const chunks = await query<ChunkRow>(
      `SELECT sc.id,
              sc.text,
              sc.spoken_at,
              sc.party_at_time,
              ls.parliament_number,
              ls.session_number
         FROM speech_chunks sc
         JOIN speeches s                    ON s.id  = sc.speech_id
         LEFT JOIN legislative_sessions ls  ON ls.id = s.session_id
        WHERE sc.id = ANY($1::uuid[])
          AND sc.politician_id = $2`,
      [chunkIds, politician_id]
    );

    if (chunks.length !== chunkIds.length) {
      return reply.code(400).send({
        error: "one or more chunks do not belong to the given politician",
        expected: chunkIds.length,
        matched: chunks.length,
      });
    }

    const politician = await queryOne<PoliticianRow>(
      `SELECT name, party FROM politicians WHERE id = $1`,
      [politician_id]
    );
    if (!politician) {
      return reply.code(404).send({ error: "politician not found" });
    }

    const sortedChunks = [...chunks].sort((a, b) => {
      // Nulls sort first (oldest-style) so the model still receives an
      // ordered sequence. getTime() returns NaN for invalid dates — map
      // to 0 defensively.
      const ad = a.spoken_at ? a.spoken_at.getTime() : 0;
      const bd = b.spoken_at ? b.spoken_at.getTime() : 0;
      return ad - bd;
    });

    const userPrompt = buildUserPrompt(politician, topic, sortedChunks);

    const controller = new AbortController();
    const timeoutHandle = setTimeout(() => controller.abort(), config.openrouter.timeoutMs);

    let openrouterResponse: Response;
    try {
      openrouterResponse = await fetch(`${config.openrouter.baseUrl}/chat/completions`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${config.openrouter.apiKey}`,
          "HTTP-Referer": config.openrouter.siteUrl,
          "X-Title": config.openrouter.appName,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model: config.openrouter.model,
          messages: [
            { role: "system", content: SYSTEM_PROMPT },
            { role: "user", content: userPrompt },
          ],
          // json_object is the widely-supported flavour; strict json_schema
          // would be tighter but free-tier models may hard-400 when they
          // don't support it.
          response_format: { type: "json_object" },
          // OpenRouter's response-healing plugin validates + repairs
          // malformed JSON server-side before it reaches us. Free-tier
          // models frequently drop keys or emit trailing commas; healing
          // catches most of it. Non-streaming only (fine — we don't
          // stream). If a model genuinely returns unrepairable output,
          // our repairModelOutput() below is the second layer and the
          // allowed-set filter is the third.
          plugins: [{ id: "response-healing" }],
          temperature: 0.2,
        }),
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timeoutHandle);
      if ((err as { name?: string }).name === "AbortError") {
        req.log.warn({ politician_id }, "[contradictions] openrouter timeout");
        return reply.code(504).send({ error: "AI service timed out" });
      }
      req.log.error({ err, politician_id }, "[contradictions] openrouter network error");
      return reply.code(502).send({ error: "AI service unreachable" });
    }
    clearTimeout(timeoutHandle);

    if (openrouterResponse.status === 401) {
      req.log.error({}, "[contradictions] openrouter 401 — check OPENROUTER_API_KEY");
      return reply.code(503).send({ error: "AI service auth failed" });
    }
    if (openrouterResponse.status === 429) {
      // Capture OpenRouter's rate-limit headers + body so we can tell
      // per-minute-burst from per-day-quota from per-model-spike in the
      // logs. Body is fetched on best-effort; errors here shouldn't
      // mask the 429 to the user.
      const rateHeaders: Record<string, string> = {};
      for (const h of [
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "retry-after",
      ]) {
        const v = openrouterResponse.headers.get(h);
        if (v) rateHeaders[h] = v;
      }
      const bodyText = await openrouterResponse.text().catch(() => "");
      req.log.warn(
        { headers: rateHeaders, body: bodyText.slice(0, 500), model: config.openrouter.model },
        "[contradictions] openrouter 429"
      );
      return reply
        .code(429)
        .send({ error: "AI service rate-limited, try again in a moment" });
    }
    if (!openrouterResponse.ok) {
      const body = await openrouterResponse.text().catch(() => "");
      req.log.error(
        { status: openrouterResponse.status, body },
        "[contradictions] openrouter upstream error"
      );
      return reply.code(502).send({ error: "AI service error" });
    }

    let raw: OpenRouterResponse;
    try {
      raw = (await openrouterResponse.json()) as OpenRouterResponse;
    } catch (err) {
      req.log.error({ err }, "[contradictions] openrouter non-json response");
      return reply.code(502).send({ error: "AI service returned non-JSON" });
    }

    const content = raw.choices?.[0]?.message?.content;
    if (typeof content !== "string") {
      req.log.error({ raw }, "[contradictions] missing choices[0].message.content");
      return reply.code(502).send({ error: "AI service returned unexpected shape" });
    }

    let modelJson: unknown;
    try {
      modelJson = JSON.parse(content);
    } catch (err) {
      req.log.error({ err, content }, "[contradictions] model output is not valid JSON");
      return reply.code(502).send({ error: "AI model returned invalid JSON" });
    }

    const repaired = repairModelOutput(modelJson);
    const output = modelOutputSchema.safeParse(repaired);
    if (!output.success) {
      req.log.error(
        { issues: output.error.issues, modelJson },
        "[contradictions] model output failed schema"
      );
      return reply.code(502).send({ error: "AI model output schema mismatch" });
    }

    // Filter out pairs that reference ids we didn't send, and self-pairs.
    // The model can hallucinate under load; dropping bad rows is better
    // than 502ing an otherwise-usable response.
    const allowed = new Set(chunkIds);
    const seen = new Set<string>();
    const filteredPairs = output.data.pairs.filter((p) => {
      if (!allowed.has(p.a_chunk_id) || !allowed.has(p.b_chunk_id)) return false;
      if (p.a_chunk_id === p.b_chunk_id) return false;
      const key = [p.a_chunk_id, p.b_chunk_id].sort().join("|");
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    return reply.send({
      model: config.openrouter.model,
      analyzed_chunk_ids: chunkIds,
      pairs: filteredPairs,
      summary: output.data.summary ?? null,
    });
  });
}
