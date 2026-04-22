import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { z } from "zod";
import { query, queryOne } from "../db.js";
import { requireAdmin, getAdminEmail } from "../middleware/user-auth.js";
import { requireCsrf } from "../lib/csrf.js";

/**
 * Admin-panel API.
 *
 * All routes under /api/v1/admin require a signed-in user with
 * users.is_admin=true (checked by requireAdmin, which composes
 * requireUser + a per-request DB lookup). Mutating routes additionally
 * require the double-submit CSRF token via a global preHandler below.
 * Bearer-token auth was removed on 2026-04-20 — the old ADMIN_TOKEN
 * flow put the credential in localStorage, readable by any XSS on the
 * same origin.
 *
 * The command catalog the frontend uses to render forms is served
 * verbatim from /commands. To keep it in sync with the worker, the
 * canonical source is in services/scanner/src/jobs_catalog.py and this
 * endpoint mirrors it. If the catalog diverges we have a bug — the
 * plan calls out a future improvement to co-locate the catalog in one
 * place and have both runtimes read it.
 */

// Keep this catalog in lockstep with services/scanner/src/jobs_catalog.py.
// Duplication is intentional for v1 — the alternative (a live HTTP call
// to the worker) couples an admin-only feature to a container that may
// be down. Worst-case drift is "UI shows a command that's not wired" —
// the worker will refuse it with "unknown command" at run time.
const COMMAND_CATALOG = [
  // hansard
  { key: "ingest-federal-hansard", category: "hansard",
    description: "Pull federal House of Commons speeches from openparliament.ca into the `speeches` table.",
    args: [
      { name: "parliament", type: "int", required: true, help: "Parliament number (e.g. 44)." },
      { name: "session", type: "int", required: true, help: "Session within the parliament (e.g. 1)." },
      { name: "since", type: "date", required: false, help: "Only fetch debates on/after this date." },
      { name: "until", type: "date", required: false, help: "Only fetch debates on/before this date." },
      { name: "limit_debates", type: "int", required: false, help: "Cap on sitting days fetched." },
      { name: "limit_speeches", type: "int", required: false, help: "Cap on TOTAL speeches ingested." },
    ],
  },
  { key: "ingest-ab-hansard", category: "hansard",
    description: "Pull Alberta Legislative Assembly speeches from PDF-only Hansard into the `speeches` table.",
    args: [
      { name: "legislature", type: "int", required: true, help: "AB Legislature number (e.g. 31)." },
      { name: "session", type: "int", required: true, help: "Session within the legislature (e.g. 2)." },
      { name: "since", type: "date", required: false, help: "Only fetch sittings on/after this date." },
      { name: "until", type: "date", required: false, help: "Only fetch sittings on/before this date." },
      { name: "limit_sittings", type: "int", required: false, help: "Cap on sitting PDFs fetched (newest-first)." },
      { name: "limit_speeches", type: "int", required: false, help: "Cap on TOTAL speeches ingested." },
    ],
  },
  { key: "ingest-bc-hansard", category: "hansard",
    description: "Pull BC Legislative Assembly Hansard (Blues + Final HTML via LIMS HDMS) into `speeches`.",
    args: [
      { name: "parliament", type: "int", required: true, help: "BC Parliament number (e.g. 43)." },
      { name: "session", type: "int", required: true, help: "Session within the parliament (e.g. 2)." },
      { name: "since", type: "date", required: false, help: "Only fetch sittings on/after this date." },
      { name: "until", type: "date", required: false, help: "Only fetch sittings on/before this date." },
      { name: "limit_sittings", type: "int", required: false, help: "Cap on sittings processed (newest-first when capped)." },
      { name: "limit_speeches", type: "int", required: false, help: "Cap on TOTAL speeches ingested." },
    ],
  },
  { key: "resolve-bc-speakers", category: "hansard",
    description: "Re-resolve politician_id on BC speeches with NULL politician_id.",
    args: [
      { name: "limit", type: "int", required: false, help: "Cap speeches scanned (smoke-test aid)." },
    ],
  },
  { key: "ingest-qc-hansard", category: "hansard",
    description: "Pull Quebec Journal des débats (HTML) into `speeches`. Bilingual source, French primary.",
    args: [
      { name: "parliament", type: "int", required: true, help: "QC parliament (législature) number (e.g. 43)." },
      { name: "session", type: "int", required: true, help: "Session within the parliament (e.g. 2)." },
      { name: "since", type: "date", required: false, help: "Only fetch sittings on/after this date." },
      { name: "until", type: "date", required: false, help: "Only fetch sittings on/before this date." },
      { name: "limit_sittings", type: "int", required: false, help: "Cap on sittings processed (newest-first when capped)." },
      { name: "limit_speeches", type: "int", required: false, help: "Cap on TOTAL speeches ingested." },
    ],
  },
  { key: "resolve-qc-speakers", category: "hansard",
    description: "Re-resolve politician_id on QC speeches with NULL politician_id.",
    args: [
      { name: "limit", type: "int", required: false, help: "Cap speeches scanned (smoke-test aid)." },
    ],
  },
  { key: "ingest-mb-hansard", category: "hansard",
    description: "Pull Manitoba Hansard (Word-exported HTML) into `speeches`. Speaker resolution via politicians.mb_assembly_slug.",
    args: [
      { name: "parliament", type: "int", required: true, help: "MB legislature number (e.g. 43)." },
      { name: "session", type: "int", required: true, help: "Session within the legislature (e.g. 3)." },
      { name: "since", type: "date", required: false, help: "Only fetch sittings on/after this date." },
      { name: "until", type: "date", required: false, help: "Only fetch sittings on/before this date." },
      { name: "limit_sittings", type: "int", required: false, help: "Cap on sittings processed." },
      { name: "limit_speeches", type: "int", required: false, help: "Cap on TOTAL speeches ingested." },
    ],
  },
  { key: "resolve-mb-speakers", category: "hansard",
    description: "Re-resolve politician_id on MB Hansard speeches with NULL politician_id.",
    args: [
      { name: "limit", type: "int", required: false, help: "Cap speeches scanned (smoke-test aid)." },
    ],
  },
  { key: "ingest-ns-hansard", category: "hansard",
    description: "Pull Nova Scotia Hansard (HTML transcripts) into `speeches`. Speaker resolution via politicians.nslegislature_slug.",
    args: [
      { name: "parliament", type: "int", required: true, help: "NS assembly number (e.g. 65 for current)." },
      { name: "session", type: "int", required: true, help: "Session within the assembly (e.g. 1)." },
      { name: "since", type: "date", required: false, help: "Only fetch sittings on/after this date." },
      { name: "until", type: "date", required: false, help: "Only fetch sittings on/before this date." },
      { name: "limit_sittings", type: "int", required: false, help: "Cap on sittings processed." },
      { name: "limit_speeches", type: "int", required: false, help: "Cap on TOTAL speeches ingested." },
    ],
  },
  { key: "resolve-ns-speakers", category: "hansard",
    description: "Re-resolve politician_id on NS Hansard speeches with NULL politician_id. Run after ingest-ns-mlas stamps new slugs.",
    args: [
      { name: "limit", type: "int", required: false, help: "Cap speeches scanned (smoke-test aid)." },
    ],
  },
  { key: "ingest-nb-hansard", category: "hansard",
    description: "Pull New Brunswick Hansard (bilingual PDF) into `speeches`. English speaker lines trigger rows; French lines become body text.",
    args: [
      { name: "legislature", type: "int", required: false, help: "NB Legislature number (pair with --session)." },
      { name: "session", type: "int", required: false, help: "Session within the legislature (requires --legislature)." },
      { name: "all_sessions_in_legislature", type: "int", required: false, help: "Every session in legislature L." },
      { name: "since", type: "date", required: false, help: "Only fetch sittings on/after this date." },
      { name: "until", type: "date", required: false, help: "Only fetch sittings on/before this date." },
      { name: "limit_sittings", type: "int", required: false, help: "Cap on sittings processed (newest-first when capped)." },
      { name: "limit_speeches", type: "int", required: false, help: "Cap on TOTAL speeches ingested." },
    ],
  },
  { key: "resolve-nb-speakers", category: "hansard",
    description: "Re-resolve politician_id on NB Hansard speeches with NULL politician_id.",
    args: [
      { name: "limit", type: "int", required: false, help: "Cap speeches scanned (smoke-test aid)." },
    ],
  },
  { key: "chunk-speeches", category: "hansard",
    description: "Split speeches.text into retrievable `speech_chunks` rows (idempotent).",
    args: [{ name: "limit", type: "int", required: false, help: "Max speeches to chunk (default: all pending)." }],
  },
  { key: "embed-speech-chunks", category: "hansard",
    description: "Fill speech_chunks.embedding via TEI (Qwen3-Embedding-0.6B). ~50 c/s end-to-end.",
    args: [
      { name: "limit", type: "int", required: false, help: "Max chunks to embed this run." },
      { name: "batch_size", type: "int", required: false, default: 32, help: "Texts per TEI /embed call." },
    ],
  },
  { key: "resolve-acting-speakers", category: "hansard",
    description: "Resolve politician_id on presiding-officer speeches (The Acting Speaker / Deputy Speaker + parenthesised MP name).",
    args: [
      { name: "limit", type: "int", required: false, help: "Cap candidate speeches scanned." },
    ],
  },
  { key: "resolve-presiding-speakers", category: "hansard",
    description: "Tie 'The Speaker' speeches to the sitting Speaker by date. Seeds politicians + politician_terms for the jurisdiction's Speaker roster, then updates NULL-politician_id rows.",
    args: [
      { name: "province", type: "enum", required: false, default: "AB", choices: ["AB", "BC", "QC", "MB", "NB", "NS"],
        help: "Jurisdiction whose Speaker roster to resolve." },
      { name: "limit", type: "int", required: false, help: "Cap candidate speeches scanned." },
    ],
  },
  { key: "refresh-coverage-stats", category: "admin",
    description: "Recompute jurisdiction_sources counts and Hansard status from live data. Drives /coverage.",
    args: [],
  },
  // bills
  { key: "ingest-ns-bills", category: "bills", description: "Nova Scotia bills via Socrata.",
    args: [{ name: "limit", type: "int", required: false, help: "Max bills this run." }] },
  { key: "ingest-ns-bills-rss", category: "bills", description: "Nova Scotia current-session RSS refresh.", args: [] },
  { key: "ingest-on-bills", category: "bills", description: "Ontario P44-S1 bills via ola.org.",
    args: [
      { name: "parliament", type: "int", required: false, default: 44, help: "Parliament number." },
      { name: "session", type: "int", required: false, default: 1, help: "Session number." },
    ],
  },
  { key: "ingest-bc-bills", category: "bills", description: "BC bills via LIMS JSON.",
    args: [
      { name: "parliament", type: "int", required: false, help: "Parliament number." },
      { name: "session", type: "int", required: false, help: "Session number." },
    ],
  },
  { key: "ingest-qc-bills", category: "bills", description: "Quebec bills via donneesquebec CSV.", args: [] },
  { key: "ingest-qc-bills-rss", category: "bills", description: "Quebec current-session RSS refresh.", args: [] },
  { key: "ingest-ab-bills", category: "bills",
    description: "Alberta bills via Assembly Dashboard. Default current session; --all-sessions backfills Legislature 1+ (~137 sessions).",
    args: [
      { name: "legislature", type: "int", required: false, help: "One specific legislature (pair with --session)." },
      { name: "session", type: "int", required: false, help: "One specific session (requires --legislature)." },
      { name: "all_sessions_in_legislature", type: "int", required: false, help: "Every session within legislature L." },
      { name: "all_sessions", type: "bool", required: false, help: "Full historical backfill (Legislature 1+, ~3.5 min)." },
      { name: "delay", type: "int", required: false, default: 2, help: "Seconds between session fetches (be polite)." },
    ],
  },
  { key: "ingest-nb-bills", category: "bills",
    description: "New Brunswick bills via legnb.ca. Default current session; --all-sessions-in-legislature L backfills a whole legislature (e.g. 56 for 2006+).",
    args: [
      { name: "legislature", type: "int", required: false, help: "One specific legislature (pair with --session)." },
      { name: "session", type: "int", required: false, help: "One specific session (requires --legislature)." },
      { name: "all_sessions_in_legislature", type: "int", required: false, help: "Every session within legislature L." },
      { name: "delay", type: "int", required: false, default: 2, help: "Seconds between per-bill detail fetches." },
    ],
  },
  { key: "ingest-nl-bills", category: "bills", description: "Newfoundland & Labrador bills via assembly.nl.ca (GA index).",
    args: [
      { name: "ga", type: "int", required: false, help: "General Assembly number (pair with --session)." },
      { name: "session", type: "int", required: false, help: "Session number (requires --ga)." },
      { name: "all_sessions_in_ga", type: "int", required: false, help: "Every session in GA G." },
      { name: "all_sessions", type: "bool", required: false, help: "Every session in the index (GA 44+, ~40 sessions)." },
    ],
  },
  { key: "ingest-nt-bills", category: "bills", description: "Northwest Territories bills via ntassembly.ca (consensus gov't, no sponsors).",
    args: [
      { name: "delay", type: "int", required: false, default: 2, help: "Seconds between per-bill fetches (be polite)." },
    ],
  },
  { key: "ingest-nu-bills", category: "bills", description: "Nunavut bills via assembly.nu.ca (consensus gov't, no sponsors; multilingual).",
    args: [
      { name: "assembly", type: "int", required: false, help: "Assembly number (default: current)." },
      { name: "session", type: "int", required: false, help: "Session number (default: current)." },
    ],
  },
  { key: "ingest-mb-bills", category: "bills",
    description: "Manitoba bills roster via web2.gov.mb.ca. Sponsors on index only; stage dates come from parse-mb-bill-events.",
    args: [
      { name: "parliament", type: "int", required: false, default: 43, help: "Legislature number (default: 43, current)." },
      { name: "session", type: "int", required: false, default: 3, help: "Session number (default: 3, current)." },
    ],
  },
  { key: "fetch-mb-billstatus-pdf", category: "bills",
    description: "Download MB billstatus.pdf into the scanner's PDF cache (once per UTC day).",
    args: [] },
  { key: "parse-mb-bill-events", category: "bills",
    description: "Parse MB billstatus.pdf → bill_events with real stage dates. Requires ingest-mb-bills first.",
    args: [
      { name: "parliament", type: "int", required: false, default: 43, help: "Legislature number (default: 43, current)." },
      { name: "session", type: "int", required: false, default: 3, help: "Session number (default: 3, current)." },
    ],
  },
  { key: "resolve-mb-bill-sponsors", category: "bills",
    description: "Link unresolved MB bill_sponsors to politicians (slug join + name-fuzz fallback).",
    args: [
      { name: "limit", type: "int", required: false, help: "Cap on rows scanned this run (default: all unresolved)." },
    ],
  },
  // enrichment
  { key: "ingest-mps", category: "enrichment", description: "Federal MPs roster from Open North.", args: [] },
  { key: "ingest-senators", category: "enrichment", description: "Canadian Senate roster.", args: [] },
  { key: "ingest-mlas", category: "enrichment", description: "Provincial/territorial legislators via Open North.", args: [] },
  { key: "ingest-mb-mlas", category: "enrichment",
    description: "Stamp politicians.mb_assembly_slug on existing MB rows; insert any missing MLAs. Prereq for ingest-mb-bills and ingest-mb-hansard.",
    args: [] },
  { key: "ingest-ns-mlas", category: "enrichment",
    description: "Stamp politicians.nslegislature_slug on seated NS MLAs by harvesting anchor slugs from current-session Hansard. Prereq for ingest-ns-hansard.",
    args: [
      { name: "parliament", type: "int", required: false, default: 65, help: "Assembly to harvest slugs from (default 65)." },
      { name: "session", type: "int", required: false, default: 1, help: "Session within the assembly (default 1)." },
      { name: "sample_sittings", type: "int", required: false, default: 5, help: "Newest sittings to scan." },
    ],
  },
  { key: "ingest-councils", category: "enrichment", description: "Municipal councillors via Open North.", args: [] },
  { key: "ingest-legislatures", category: "enrichment", description: "Full provincial/territorial legislature ingest.", args: [] },
  { key: "harvest-personal-socials", category: "enrichment", description: "Scrape personal sites for social handles.",
    args: [{ name: "limit", type: "int", required: false, help: "Max politicians this run." }] },
  // socials audit + tiered backfill
  { key: "audit-socials", category: "enrichment",
    description: "Snapshot social-media coverage; refresh v_socials_missing view.",
    args: [{ name: "no_csv", type: "bool", required: false, help: "Skip CSV export; print tables only." }] },
  { key: "enrich-socials-all", category: "enrichment",
    description: "Tier-1: wikidata + openparliament + masto-host enrichment. Zero LLM cost.", args: [] },
  { key: "probe-missing-socials", category: "enrichment",
    description: "Tier-2: pattern-probe candidate URLs for missing socials. Zero LLM cost.",
    args: [
      { name: "platform", type: "str", required: false, default: "bluesky",
        help: "bluesky | twitter | facebook | instagram | youtube | threads" },
      { name: "limit", type: "int", required: false, default: 500, help: "Max missing rows to probe this run." },
      { name: "dry_run", type: "bool", required: false, help: "Print would-be inserts without writing." },
    ] },
  { key: "agent-missing-socials", category: "enrichment",
    description: "Tier-3: Sonnet agent + web_search fills residual missing handles. Requires ANTHROPIC_API_KEY.",
    args: [
      { name: "platform", type: "str", required: false, help: "Focus one platform (omit for all-missing)." },
      { name: "batch_size", type: "int", required: false, default: 10, help: "Politicians per agent call (max 25)." },
      { name: "max_batches", type: "int", required: false, default: 20, help: "Hard cap on agent calls per run." },
      { name: "model", type: "str", required: false, help: "Override the default Claude model." },
      { name: "dry_run", type: "bool", required: false, help: "Print candidate hits without inserting." },
    ] },
  { key: "verify-socials", category: "enrichment",
    description: "Liveness check on politician_socials URLs. Writes social_dead change rows on live→dead flips.",
    args: [
      { name: "limit", type: "int", required: false, default: 500, help: "Max rows to verify per run." },
      { name: "stale_hours", type: "int", required: false, default: 168, help: "Re-verify if older than N hours." },
    ] },
  // maintenance
  { key: "refresh-views", category: "maintenance", description: "Refresh map materialized views.", args: [] },
  { key: "seed-orgs", category: "maintenance", description: "Re-apply referendum/advocacy orgs seed.", args: [] },
  { key: "backfill-terms", category: "maintenance",
    description: "One-time: open an initial politician_terms row for every active politician without an existing open term. Prereq for party-at-time queries.",
    args: [] },
  { key: "backfill-politician-photos", category: "maintenance",
    description: "Mirror upstream politician portraits to the local /assets volume; re-fetch stale rows (>30 days) on each run. Idempotent.",
    args: [
      { name: "limit", type: "int", required: false, help: "Cap politicians processed this run." },
      { name: "stale_days", type: "int", required: false, default: 30, help: "Re-fetch if last fetch is older than N days." },
      { name: "politician_id", type: "str", required: false, help: "Process a single politician by UUID." },
      { name: "concurrency", type: "int", required: false, default: 4, help: "Parallel fetches. Per-host spacing still applies." },
    ],
  },
  { key: "scan", category: "maintenance", description: "Infrastructure scan across tracked websites.",
    args: [
      { name: "limit", type: "int", required: false, help: "Max sites this run." },
      { name: "stale_hours", type: "int", required: false, default: 6, help: "Re-scan if older than N hours." },
    ],
  },
];

const COMMAND_KEYS = new Set(COMMAND_CATALOG.map(c => c.key));

// ── Zod schemas ────────────────────────────────────────────────────

const jobsListQuery = z.object({
  status: z.enum(["queued", "running", "succeeded", "failed", "cancelled"]).optional(),
  schedule_id: z.string().uuid().optional(),
  limit: z.coerce.number().int().min(1).max(500).default(100),
});

const jobCreateBody = z.object({
  command: z.string(),
  args: z.record(z.string(), z.any()).default({}),
  priority: z.coerce.number().int().min(0).max(100).default(10),
});

const scheduleCreateBody = z.object({
  name: z.string().min(1).max(200),
  command: z.string(),
  args: z.record(z.string(), z.any()).default({}),
  cron: z.string().regex(
    /^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$/,
    "cron must be a 5-field expression (m h dom mon dow)"
  ),
  enabled: z.boolean().optional().default(true),
});

const schedulePatchBody = z.object({
  name: z.string().min(1).max(200).optional(),
  args: z.record(z.string(), z.any()).optional(),
  cron: z.string().regex(/^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$/).optional(),
  enabled: z.boolean().optional(),
});

// ── Routes ─────────────────────────────────────────────────────────

export default async function adminRoutes(app: FastifyInstance) {
  // Gate every route on "signed-in user with is_admin=true".
  app.addHook("preHandler", requireAdmin);
  // Mutating routes additionally require CSRF. Hook order matters —
  // this runs after requireAdmin, so a non-admin caller gets 403
  // (wrong role) rather than 403 (missing CSRF), which is the more
  // useful error. GET/HEAD are safe methods per RFC 9110 §9.2.1;
  // OPTIONS is handled by @fastify/cors before we see it.
  app.addHook("preHandler", async (req: FastifyRequest, reply: FastifyReply) => {
    const m = req.method.toUpperCase();
    if (m === "GET" || m === "HEAD" || m === "OPTIONS") return;
    return requireCsrf(req, reply);
  });

  app.get("/commands", async () => ({ commands: COMMAND_CATALOG }));

  // ── Jobs ───────────────────────────────────────────────────────
  app.get("/jobs", async (req, reply) => {
    const q = jobsListQuery.safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);
    const { status, schedule_id, limit } = q.data;

    const params: unknown[] = [];
    const where: string[] = [];
    if (status) { params.push(status); where.push(`status = $${params.length}`); }
    if (schedule_id) { params.push(schedule_id); where.push(`schedule_id = $${params.length}`); }
    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
    params.push(limit);
    const rows = await query(
      `SELECT id, command, args, status, priority, schedule_id, requested_by,
              queued_at, started_at, finished_at, exit_code,
              -- size-cap the tails at list time to keep payloads small
              LEFT(COALESCE(stdout_tail,''), 500) AS stdout_snippet,
              LEFT(COALESCE(stderr_tail,''), 500) AS stderr_snippet,
              error
         FROM scanner_jobs
         ${whereSql}
         ORDER BY queued_at DESC
         LIMIT $${params.length}`,
      params as any,
    );
    return { jobs: rows };
  });

  app.post("/jobs", async (req, reply) => {
    const parsed = jobCreateBody.safeParse(req.body);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const { command, args, priority } = parsed.data;
    if (!COMMAND_KEYS.has(command)) {
      return reply.badRequest(`unknown command: ${command}`);
    }
    const actor = getAdminEmail(req) ?? "admin";
    const row = await queryOne<{ id: string }>(
      `INSERT INTO scanner_jobs (command, args, status, priority, requested_by)
       VALUES ($1, $2::jsonb, 'queued', $3, $4)
       RETURNING id`,
      [command, JSON.stringify(args), priority, actor] as any,
    );
    return reply.code(201).send({ id: row?.id });
  });

  app.get("/jobs/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();
    const row = await queryOne(
      `SELECT id, command, args, status, priority, schedule_id, requested_by,
              queued_at, started_at, finished_at, exit_code,
              stdout_tail, stderr_tail, error
         FROM scanner_jobs WHERE id = $1`,
      [id] as any,
    );
    if (!row) return reply.notFound();
    return row;
  });

  app.post("/jobs/:id/cancel", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();
    const row = await queryOne(
      `UPDATE scanner_jobs
          SET status = 'cancelled', finished_at = now()
        WHERE id = $1 AND status = 'queued'
        RETURNING id, status`,
      [id] as any,
    );
    if (!row) {
      return reply.code(409).send({ error: "job not queued (already running or terminal)" });
    }
    return row;
  });

  // ── Schedules ───────────────────────────────────────────────────
  app.get("/schedules", async () => {
    const rows = await query(
      `SELECT id, name, command, args, cron, enabled,
              last_enqueued_at, next_run_at,
              created_by, created_at, updated_at
         FROM scanner_schedules
         ORDER BY name`
    );
    return { schedules: rows };
  });

  app.post("/schedules", async (req, reply) => {
    const parsed = scheduleCreateBody.safeParse(req.body);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const { name, command, args, cron, enabled } = parsed.data;
    if (!COMMAND_KEYS.has(command)) {
      return reply.badRequest(`unknown command: ${command}`);
    }
    const actor = getAdminEmail(req) ?? "admin";
    const row = await queryOne<{ id: string }>(
      `INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by)
       VALUES ($1, $2, $3::jsonb, $4, $5, $6)
       RETURNING id`,
      [name, command, JSON.stringify(args), cron.trim(), enabled, actor] as any,
    );
    return reply.code(201).send({ id: row?.id });
  });

  app.patch("/schedules/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();
    const parsed = schedulePatchBody.safeParse(req.body);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const fields: string[] = [];
    const params: unknown[] = [];
    const body = parsed.data;
    if (body.name !== undefined) { params.push(body.name); fields.push(`name = $${params.length}`); }
    if (body.args !== undefined) { params.push(JSON.stringify(body.args)); fields.push(`args = $${params.length}::jsonb`); }
    if (body.cron !== undefined) {
      params.push(body.cron.trim());
      fields.push(`cron = $${params.length}`);
      // Force next_run_at recompute on the worker's next poll by clearing it.
      fields.push(`next_run_at = NULL`);
    }
    if (body.enabled !== undefined) { params.push(body.enabled); fields.push(`enabled = $${params.length}`); }
    if (!fields.length) return reply.badRequest("no fields to update");
    params.push(id);
    const row = await queryOne(
      `UPDATE scanner_schedules SET ${fields.join(", ")} WHERE id = $${params.length}
       RETURNING id, name, command, args, cron, enabled, next_run_at`,
      params as any,
    );
    if (!row) return reply.notFound();
    return row;
  });

  app.delete("/schedules/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();
    const res = await query(
      `DELETE FROM scanner_schedules WHERE id = $1 RETURNING id`,
      [id] as any,
    );
    if (!res.length) return reply.notFound();
    return reply.code(204).send();
  });

  // ── Dashboard stats ─────────────────────────────────────────────
  app.get("/stats", async () => {
    // Single trip for low-latency dashboard load.
    const [
      speeches, chunks, jobs, jurisdictions, recentFailures,
    ] = await Promise.all([
      queryOne<{ total: number }>(`SELECT COUNT(*)::int AS total FROM speeches`),
      queryOne<{ total: number; embedded: number; pending: number }>(
        `SELECT COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE embedding IS NOT NULL)::int AS embedded,
                COUNT(*) FILTER (WHERE embedding IS NULL)::int     AS pending
           FROM speech_chunks`
      ),
      queryOne<{ queued: number; running: number; succeeded_24h: number; failed_24h: number }>(
        `SELECT
            COUNT(*) FILTER (WHERE status = 'queued')::int   AS queued,
            COUNT(*) FILTER (WHERE status = 'running')::int  AS running,
            COUNT(*) FILTER (WHERE status = 'succeeded' AND finished_at > now() - interval '24 hours')::int AS succeeded_24h,
            COUNT(*) FILTER (WHERE status = 'failed' AND finished_at > now() - interval '24 hours')::int    AS failed_24h
           FROM scanner_jobs`
      ),
      queryOne<{ live: number; total: number }>(
        `SELECT COUNT(*) FILTER (WHERE bills_status = 'live')::int AS live,
                COUNT(*)::int AS total
           FROM jurisdiction_sources`
      ),
      query(
        `SELECT id, command, finished_at, error
           FROM scanner_jobs
          WHERE status = 'failed' AND finished_at > now() - interval '24 hours'
          ORDER BY finished_at DESC LIMIT 5`
      ),
    ]);
    return {
      speeches: speeches?.total ?? 0,
      chunks: {
        total: chunks?.total ?? 0,
        embedded: chunks?.embedded ?? 0,
        pending: chunks?.pending ?? 0,
      },
      jobs: {
        queued: jobs?.queued ?? 0,
        running: jobs?.running ?? 0,
        succeeded_24h: jobs?.succeeded_24h ?? 0,
        failed_24h: jobs?.failed_24h ?? 0,
      },
      jurisdictions: {
        live: jurisdictions?.live ?? 0,
        total: jurisdictions?.total ?? 0,
      },
      recent_failures: recentFailures,
    };
  });

  // ── Socials audit + review queue ───────────────────────────────
  // The Tier-2 probe and Tier-3 agent can land rows with
  // flagged_low_confidence=true. This endpoint surfaces them for
  // human spot-checking; approve (clear flag) / reject (delete).
  app.get("/socials/coverage", async () => {
    const [total, withAny, sources, platforms] = await Promise.all([
      queryOne<{ n: number }>(
        `SELECT COUNT(*)::int AS n FROM politicians WHERE is_active = true`),
      queryOne<{ n: number }>(
        `SELECT COUNT(DISTINCT politician_id)::int AS n
           FROM politician_socials
          WHERE politician_id IN (SELECT id FROM politicians WHERE is_active = true)`),
      query<{ source: string; n: number; flagged: number }>(
        `SELECT COALESCE(source, '<null>') AS source,
                COUNT(*)::int AS n,
                COUNT(*) FILTER (WHERE flagged_low_confidence = true)::int AS flagged
           FROM politician_socials
          GROUP BY source
          ORDER BY n DESC`),
      query<{ platform: string; n: number; flagged: number }>(
        `SELECT platform,
                COUNT(*)::int AS n,
                COUNT(*) FILTER (WHERE flagged_low_confidence = true)::int AS flagged
           FROM politician_socials
          GROUP BY platform
          ORDER BY n DESC`),
    ]);
    return {
      total_active: total?.n ?? 0,
      with_any_social: withAny?.n ?? 0,
      by_source: sources,
      by_platform: platforms,
    };
  });

  const flaggedListQuery = z.object({
    platform: z.string().optional(),
    limit: z.coerce.number().int().min(1).max(500).default(50),
    offset: z.coerce.number().int().min(0).default(0),
  });

  app.get("/socials/flagged", async (req, reply) => {
    const q = flaggedListQuery.safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);
    const { platform, limit, offset } = q.data;
    const params: unknown[] = [];
    const where: string[] = ["s.flagged_low_confidence = true"];
    if (platform) { params.push(platform); where.push(`s.platform = $${params.length}`); }
    params.push(limit); const limIdx = params.length;
    params.push(offset); const offIdx = params.length;
    const rows = await query(
      `SELECT s.id, s.politician_id, s.platform, s.handle, s.url,
              s.source, s.confidence::float AS confidence,
              s.evidence_url, s.discovered_at,
              p.name AS politician_name,
              p.level, p.province_territory, p.party, p.constituency_name
         FROM politician_socials s
         JOIN politicians p ON p.id = s.politician_id
        WHERE ${where.join(" AND ")}
        ORDER BY s.confidence ASC, s.discovered_at DESC NULLS LAST
        LIMIT $${limIdx} OFFSET $${offIdx}`,
      params as any,
    );
    return { items: rows };
  });

  app.post("/socials/:id/approve", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();
    const row = await queryOne(
      `UPDATE politician_socials
          SET flagged_low_confidence = false,
              confidence = GREATEST(confidence, 1.0),
              updated_at = now()
        WHERE id = $1
        RETURNING id, flagged_low_confidence, confidence`,
      [id] as any,
    );
    if (!row) return reply.notFound();
    return row;
  });

  app.post("/socials/:id/reject", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();
    const row = await queryOne(
      `DELETE FROM politician_socials WHERE id = $1 RETURNING id`,
      [id] as any,
    );
    if (!row) return reply.notFound();
    return reply.code(204).send();
  });

  // ── Corrections review ──────────────────────────────────────────
  // List / triage / resolve user-submitted corrections. Deep-linking
  // to the subject is the frontend's responsibility (see
  // AdminCorrections.tsx) — we just surface the foreign-key fields.

  const correctionListQuery = z.object({
    status: z.enum(["pending", "triaged", "applied", "rejected", "duplicate", "spam", "all"])
      .optional()
      .default("pending"),
    limit: z.coerce.number().int().min(1).max(200).default(50),
    offset: z.coerce.number().int().min(0).default(0),
  });

  const correctionPatchBody = z.object({
    status: z.enum(["pending", "triaged", "applied", "rejected", "duplicate", "spam"]),
    reviewer_notes: z.string().trim().max(2000).optional().nullable(),
  });

  const TERMINAL_STATUSES = new Set(["applied", "rejected", "duplicate", "spam"]);

  app.get("/corrections", async (req, reply) => {
    const parsed = correctionListQuery.safeParse(req.query);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid query" });
    }
    const { status, limit, offset } = parsed.data;
    const whereSql = status === "all" ? "" : "WHERE cs.status = $1";
    const params: unknown[] = status === "all" ? [] : [status];

    const rows = await query(
      `
      SELECT cs.id, cs.subject_type, cs.subject_id, cs.issue, cs.proposed_fix,
             cs.evidence_url, cs.status, cs.reviewer_notes, cs.reviewed_by,
             cs.submitter_name, cs.submitter_email, cs.user_id, cs.source,
             cs.received_at, cs.resolved_at,
             u.email AS user_email, u.display_name AS user_display_name,
             p.name  AS politician_name
        FROM correction_submissions cs
        LEFT JOIN users u ON u.id = cs.user_id
        LEFT JOIN politicians p
               ON cs.subject_type = 'politician' AND p.id = cs.subject_id
      ${whereSql}
      ORDER BY cs.received_at DESC
      LIMIT ${limit} OFFSET ${offset}
      `,
      params as any,
    );
    return { corrections: rows };
  });

  app.get("/corrections/stats", async () => {
    const rows = await query<{ status: string; n: string }>(
      `SELECT status, count(*)::text AS n
         FROM correction_submissions
        GROUP BY status`,
    );
    const out: Record<string, number> = {
      pending: 0, triaged: 0, applied: 0, rejected: 0, duplicate: 0, spam: 0,
    };
    for (const r of rows) out[r.status] = Number(r.n);
    return out;
  });

  app.patch("/corrections/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();

    const parsed = correctionPatchBody.safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid body" });
    }
    const { status, reviewer_notes } = parsed.data;

    // Single UPDATE keeps status + notes + reviewed_by + resolved_at
    // in sync. resolved_at is set only when transitioning into a
    // terminal state, and cleared if we ever walk backwards.
    const resolvedExpr = TERMINAL_STATUSES.has(status)
      ? "now()"
      : "NULL";
    const actor = getAdminEmail(req) ?? "admin";

    const row = await queryOne(
      `
      UPDATE correction_submissions
         SET status         = $1,
             reviewer_notes = $2,
             reviewed_by    = $3,
             resolved_at    = ${resolvedExpr}
       WHERE id = $4
       RETURNING id, subject_type, subject_id, issue, proposed_fix,
                 evidence_url, status, reviewer_notes, reviewed_by,
                 received_at, resolved_at
      `,
      [status, reviewer_notes ?? null, actor, id] as any,
    );
    if (!row) return reply.notFound();
    return row;
  });
}
