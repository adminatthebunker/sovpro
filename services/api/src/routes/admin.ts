import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query, queryOne } from "../db.js";
import { requireAdminToken } from "../middleware/admin-auth.js";

/**
 * Admin-panel API.
 *
 * All routes under /api/v1/admin require a Bearer token matching
 * config.adminToken (checked by requireAdminToken). The exception is
 * /login — that endpoint exists so the UI can verify the pasted token
 * before persisting it into localStorage, so it runs the same
 * middleware and just returns {ok:true} on success.
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
  { key: "chunk-speeches", category: "hansard",
    description: "Split speeches.text into retrievable `speech_chunks` rows (idempotent).",
    args: [{ name: "limit", type: "int", required: false, help: "Max speeches to chunk (default: all pending)." }],
  },
  { key: "embed-speech-chunks", category: "hansard",
    description: "Fill speech_chunks.embedding via the local BGE-M3 service.",
    args: [
      { name: "limit", type: "int", required: false, help: "Max chunks to embed this run." },
      { name: "batch_size", type: "int", required: false, default: 32, help: "Texts per /embed call." },
    ],
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
  { key: "ingest-nb-bills", category: "bills", description: "New Brunswick bills via legnb.ca.",
    args: [
      { name: "legislature", type: "int", required: false, help: "Legislature number." },
      { name: "session", type: "int", required: false, help: "Session number." },
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
  // enrichment
  { key: "ingest-mps", category: "enrichment", description: "Federal MPs roster from Open North.", args: [] },
  { key: "ingest-senators", category: "enrichment", description: "Canadian Senate roster.", args: [] },
  { key: "ingest-mlas", category: "enrichment", description: "Provincial/territorial legislators via Open North.", args: [] },
  { key: "ingest-councils", category: "enrichment", description: "Municipal councillors via Open North.", args: [] },
  { key: "ingest-legislatures", category: "enrichment", description: "Full provincial/territorial legislature ingest.", args: [] },
  { key: "harvest-personal-socials", category: "enrichment", description: "Scrape personal sites for social handles.",
    args: [{ name: "limit", type: "int", required: false, help: "Max politicians this run." }] },
  // maintenance
  { key: "refresh-views", category: "maintenance", description: "Refresh map materialized views.", args: [] },
  { key: "seed-orgs", category: "maintenance", description: "Re-apply referendum/advocacy orgs seed.", args: [] },
  { key: "backfill-terms", category: "maintenance",
    description: "One-time: open an initial politician_terms row for every active politician without an existing open term. Prereq for party-at-time queries.",
    args: [] },
  { key: "scan", category: "maintenance", description: "Infrastructure scan across tracked websites.",
    args: [
      { name: "limit", type: "int", required: false, help: "Max sites this run." },
      { name: "stale_hours", type: "int", required: false, default: 6, help: "Re-scan if older than N hours." },
    ],
  },
];

const COMMAND_KEYS = new Set(COMMAND_CATALOG.map(c => c.key));

// ── Zod schemas ────────────────────────────────────────────────────

const loginBody = z.object({ token: z.string().min(1) });

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
  // Gate every route with the bearer-token check.
  app.addHook("preHandler", requireAdminToken);

  // Symbolic endpoint the UI calls first to verify a pasted token.
  app.post("/login", async (req, reply) => {
    const parsed = loginBody.safeParse(req.body);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    // The real check already ran in requireAdminToken via the Authorization
    // header. If we got here, we're good.
    // We DO NOT compare parsed.data.token to anything — the handshake is
    // "send it in the Authorization header you'll use going forward".
    return { ok: true };
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
    const row = await queryOne<{ id: string }>(
      `INSERT INTO scanner_jobs (command, args, status, priority, requested_by)
       VALUES ($1, $2::jsonb, 'queued', $3, 'admin')
       RETURNING id`,
      [command, JSON.stringify(args), priority] as any,
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
    const row = await queryOne<{ id: string }>(
      `INSERT INTO scanner_schedules (name, command, args, cron, enabled, created_by)
       VALUES ($1, $2, $3::jsonb, $4, $5, 'admin')
       RETURNING id`,
      [name, command, JSON.stringify(args), cron.trim(), enabled] as any,
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
}
