import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { z } from "zod";
import { pool, query, queryOne } from "../db.js";
import { requireAdmin, getAdminEmail } from "../middleware/user-auth.js";
import { requireCsrf } from "../lib/csrf.js";
import {
  getBalance,
  grantAdminCredit,
  grantCorrectionReward,
  listLedgerEntries,
} from "../lib/credits.js";
import { sendCorrectionApprovedEmail } from "../lib/email.js";
import { config } from "../config.js";

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
  { key: "enrich-ab-mlas", category: "enrichment",
    description: "Fetch /member-information?mid=NNNN per AB MLA — photo, party, constituency, cabinet/Speaker offices into politicians + politician_terms.",
    args: [
      { name: "mid", type: "string", required: false, help: "Process a single ab_assembly_mid (smoke test)." },
      { name: "limit", type: "int", required: false, help: "Cap number of MLAs processed this run." },
      { name: "delay", type: "float", required: false, help: "Seconds between page fetches (default 1.0)." },
      { name: "refresh", type: "bool", required: false, help: "Re-fetch even MLAs already enriched." },
    ],
  },
  { key: "merge-ab-presiding-stubs", category: "maintenance",
    description: "One-time reconciliation of presiding-officer-seed:AB:* stubs into their MID-keyed twins. Speeches + chunks reassign; speaker_role preserved.",
    args: [
      { name: "dry_run", type: "bool", required: false, help: "Report stub→twin pairs without modifying any rows." },
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
  { key: "resolve-mb-speakers-dated", category: "hansard",
    description: "Date-windowed MB speaker resolver. Uses politician_terms to disambiguate historical surnames after the former-MLAs backfill.",
    args: [
      { name: "limit", type: "int", required: false, help: "Cap candidate speeches scanned (smoke-test aid)." },
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
  { key: "ingest-nl-hansard", category: "hansard",
    description: "Pull Newfoundland & Labrador Hansard (era-branching: Word-exported MsoNormal + legacy FrontPage) into `speeches`. Speaker resolution via (first_initial, surname) against date-windowed NL politician_terms.",
    args: [
      { name: "ga", type: "int", required: true, help: "NL General Assembly number (e.g. 51)." },
      { name: "session", type: "int", required: true, help: "Session within the GA (e.g. 1)." },
      { name: "since", type: "date", required: false, help: "Only fetch sittings on/after this date." },
      { name: "until", type: "date", required: false, help: "Only fetch sittings on/before this date." },
      { name: "limit_sittings", type: "int", required: false, help: "Cap on sittings processed." },
      { name: "limit_speeches", type: "int", required: false, help: "Cap on TOTAL speeches ingested." },
    ],
  },
  { key: "resolve-nl-speakers", category: "hansard",
    description: "Re-resolve politician_id on NL Hansard speeches with NULL politician_id (skips group markers + presiding-role rows).",
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
      { name: "province", type: "enum", required: false, default: "AB", choices: ["AB", "BC", "QC", "MB", "NB", "NL", "NS"],
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
  { key: "ingest-mb-former-mlas", category: "enrichment",
    description: "Backfill ~800 historical MB MLAs from mla_bio_living/deceased.html. Name-matches current MLAs before inserting; new rows keyed on lastname-firstname slugs. Prereq for pre-2023 MB Hansard backfill.",
    args: [
      { name: "living", type: "bool", required: false, default: true, help: "Include the living-MLAs bio page." },
      { name: "deceased", type: "bool", required: false, default: true, help: "Include the deceased-MLAs bio page." },
      { name: "delay", type: "float", required: false, default: 1.0, help: "Seconds between page fetches." },
    ] },
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

    const params: (string | number | boolean | null | string[])[] = [];
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
    const params: (string | number | boolean | null | string[])[] = [];
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
    const params: (string | number | boolean | null | string[])[] = [];
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

    // The whole status-flip + credit-grant is one transaction: if
    // the reward insert fails for any reason other than the
    // idempotency unique-violation (which the helper swallows), the
    // status change rolls back with it. That keeps "correction is
    // applied in DB" and "reward row exists in ledger" in lockstep.
    interface CorrectionRowFull {
      id: string;
      subject_type: string;
      subject_id: string | null;
      issue: string;
      proposed_fix: string | null;
      evidence_url: string | null;
      status: string;
      reviewer_notes: string | null;
      reviewed_by: string | null;
      received_at: Date;
      resolved_at: Date | null;
      user_id: string | null;
      submitter_email: string | null;
    }

    const client = await pool.connect();
    let updated: CorrectionRowFull | null = null;
    let rewardGranted = false;
    let rewardAlreadyGranted = false;
    const rewardAmount = config.corrections.rewardCredits;

    try {
      await client.query("BEGIN");

      const res = await client.query<CorrectionRowFull>(
        `
        UPDATE correction_submissions
           SET status         = $1,
               reviewer_notes = $2,
               reviewed_by    = $3,
               resolved_at    = ${resolvedExpr}
         WHERE id = $4
         RETURNING id, subject_type, subject_id, issue, proposed_fix,
                   evidence_url, status, reviewer_notes, reviewed_by,
                   received_at, resolved_at, user_id, submitter_email
        `,
        [status, reviewer_notes ?? null, actor, id]
      );
      updated = res.rows[0] ?? null;

      if (!updated) {
        await client.query("ROLLBACK");
        return reply.notFound();
      }

      // Grant the reward only when transitioning to applied on a
      // non-anonymous row with a positive configured reward.
      if (
        status === "applied" &&
        updated.user_id &&
        rewardAmount > 0
      ) {
        const reasonNote = `Correction accepted (${updated.subject_type})`;
        const grant = await grantCorrectionReward(
          {
            userId: updated.user_id,
            correctionId: updated.id,
            credits: rewardAmount,
            reason: reasonNote,
          },
          client
        );
        rewardGranted = grant.ledgerEntryId !== null;
        rewardAlreadyGranted = grant.alreadyGranted;
      }

      await client.query("COMMIT");
    } catch (err) {
      await client.query("ROLLBACK").catch(() => {});
      throw err;
    } finally {
      client.release();
    }

    // Fire-and-forget notification email. Only on fresh grants —
    // idempotent re-runs don't re-email. Suppressed when the user's
    // address has hard-bounced (mirrors alerts-worker discipline).
    if (rewardGranted && updated?.user_id) {
      void (async () => {
        try {
          const recipient = await queryOne<{
            email: string;
            display_name: string | null;
            email_bounced_at: Date | null;
            balance: string | null;
          }>(
            `SELECT u.email,
                    u.display_name,
                    u.email_bounced_at,
                    (SELECT COALESCE(SUM(delta), 0)::text
                       FROM credit_ledger
                      WHERE user_id = u.id
                        AND state IN ('committed','held')) AS balance
               FROM users u
              WHERE u.id = $1`,
            [updated!.user_id!]
          );
          if (!recipient) {
            req.log.warn(
              { correction_id: updated!.id },
              "[correction-reward] user row missing for notification"
            );
            return;
          }
          if (recipient.email_bounced_at) {
            req.log.warn(
              { correction_id: updated!.id, user_id: updated!.user_id },
              "[correction-reward] skipping email — address has hard-bounced"
            );
            return;
          }
          await sendCorrectionApprovedEmail(
            {
              to: recipient.email,
              displayName: recipient.display_name,
              correctionIssue: updated!.issue,
              creditsGranted: rewardAmount,
              newBalance: Number(recipient.balance ?? 0),
              accountUrl: `${config.publicSiteUrl}/account/credits`,
            },
            req.log
          );
          req.log.info(
            { correction_id: updated!.id, user_id: updated!.user_id },
            "[correction-reward] notification email dispatched"
          );
        } catch (err) {
          req.log.warn(
            { err, correction_id: updated!.id },
            "[correction-reward] email dispatch failed — credit grant unaffected"
          );
        }
      })();
    }

    // Strip user_id + submitter_email from the response — the
    // existing admin correction list has its own enriched endpoint,
    // and we don't need to start shipping PII here that wasn't
    // previously returned.
    const {
      user_id: _uid,
      submitter_email: _semail,
      ...publicFields
    } = updated;

    return reply.send({
      ...publicFields,
      credit_reward: {
        credits: rewardAmount,
        granted: rewardGranted,
        already_granted: rewardAlreadyGranted,
        eligible: status === "applied" && Boolean(updated.user_id),
      },
    });
  });

  // ── Users (for credit grants + rate-limit tier adjustments) ────
  //
  // Scoped to what the billing-rail admin UI needs: email search
  // picker, per-user detail with balance + ledger, credit grant,
  // rate-limit tier bump. Non-admin users see nothing from these —
  // requireAdmin gates the whole router.

  app.get("/users", async (req, reply) => {
    const q = z
      .object({
        q: z.string().trim().min(1).max(200).optional(),
        limit: z.coerce.number().int().min(1).max(100).default(20),
      })
      .safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);

    const params: (string | number | boolean | null | string[])[] = [];
    const conditions: string[] = [];
    if (q.data.q) {
      params.push(`%${q.data.q.toLowerCase()}%`);
      conditions.push(`email ILIKE $${params.length}`);
    }
    params.push(q.data.limit);
    const whereSql = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
    const rows = await query(
      `SELECT id, email, display_name, is_admin, rate_limit_tier,
              stripe_customer_id, created_at, last_login_at
         FROM users
         ${whereSql}
         ORDER BY created_at DESC
         LIMIT $${params.length}`,
      params as any,
    );
    return { users: rows };
  });

  app.get("/users/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();

    const user = await queryOne(
      `SELECT id, email, display_name, is_admin, rate_limit_tier,
              stripe_customer_id, created_at, last_login_at
         FROM users WHERE id = $1`,
      [id],
    );
    if (!user) return reply.notFound();

    const [balance, history] = await Promise.all([
      getBalance(id),
      listLedgerEntries(id, 100),
    ]);
    return { user, balance, ledger: history };
  });

  app.post("/users/:id/grant-credits", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();

    const parsed = z
      .object({
        amount: z.number().int().positive().max(100_000),
        reason: z.string().trim().min(3).max(500),
      })
      .safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid body", details: parsed.error.flatten() });
    }

    const target = await queryOne<{ id: string }>(
      `SELECT id FROM users WHERE id = $1`,
      [id],
    );
    if (!target) return reply.notFound();

    // The acting admin's id — pulled from the request after
    // requireAdmin has validated the session. We need the id (not
    // just email) for the created_by_admin_id FK.
    const actingAdminEmail = getAdminEmail(req) ?? null;
    if (!actingAdminEmail) return reply.code(403).send({ error: "admin identity lost" });
    const actingAdmin = await queryOne<{ id: string }>(
      `SELECT id FROM users WHERE email = $1`,
      [actingAdminEmail],
    );
    if (!actingAdmin) return reply.code(403).send({ error: "admin row missing" });

    const ledgerId = await grantAdminCredit({
      userId: id,
      adminId: actingAdmin.id,
      credits: parsed.data.amount,
      reason: parsed.data.reason,
    });

    req.log.info(
      { target_user_id: id, admin_email: actingAdminEmail, amount: parsed.data.amount, ledger_id: ledgerId },
      "[admin] credits granted",
    );

    const balance = await getBalance(id);
    return reply.send({ ledger_entry_id: ledgerId, balance });
  });

  app.patch("/users/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();

    const parsed = z
      .object({
        rate_limit_tier: z.enum(["default", "extended", "unlimited", "suspended"]),
      })
      .safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid body" });
    }

    const row = await queryOne(
      `UPDATE users
          SET rate_limit_tier = $1
        WHERE id = $2
        RETURNING id, email, rate_limit_tier`,
      [parsed.data.rate_limit_tier, id],
    );
    if (!row) return reply.notFound();

    req.log.info(
      { target_user_id: id, tier: parsed.data.rate_limit_tier, admin: getAdminEmail(req) },
      "[admin] rate_limit_tier updated",
    );
    return row;
  });

  // ── Rate-limit increase requests ───────────────────────────────

  app.get("/rate-limit-requests", async (req, reply) => {
    const q = z
      .object({
        status: z.enum(["pending", "approved", "denied"]).optional(),
        limit: z.coerce.number().int().min(1).max(100).default(50),
      })
      .safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);

    const params: (string | number | boolean | null | string[])[] = [];
    const conditions: string[] = [];
    if (q.data.status) {
      params.push(q.data.status);
      conditions.push(`r.status = $${params.length}`);
    }
    params.push(q.data.limit);
    const whereSql = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
    const rows = await query(
      `SELECT r.id, r.user_id, u.email, r.reason, r.requested_tier,
              r.status, r.admin_response, r.created_at, r.resolved_at
         FROM rate_limit_increase_requests r
         JOIN users u ON u.id = r.user_id
         ${whereSql}
         ORDER BY r.created_at DESC
         LIMIT $${params.length}`,
      params as any,
    );
    return { requests: rows };
  });

  app.patch("/rate-limit-requests/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.notFound();

    const parsed = z
      .object({
        status: z.enum(["approved", "denied"]),
        admin_response: z.string().trim().min(1).max(1000),
        // When approving, the admin can also bump the user's tier in
        // the same action. Declined requests just update the request
        // row and leave the user's tier untouched.
        apply_tier: z.enum(["extended", "unlimited"]).optional(),
      })
      .safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid body", details: parsed.error.flatten() });
    }

    const actingAdminEmail = getAdminEmail(req) ?? null;
    if (!actingAdminEmail) return reply.code(403).send({ error: "admin identity lost" });
    const actingAdmin = await queryOne<{ id: string }>(
      `SELECT id FROM users WHERE email = $1`,
      [actingAdminEmail],
    );
    if (!actingAdmin) return reply.code(403).send({ error: "admin row missing" });

    const requestRow = await queryOne<{ user_id: string; status: string }>(
      `SELECT user_id, status FROM rate_limit_increase_requests WHERE id = $1`,
      [id],
    );
    if (!requestRow) return reply.notFound();
    if (requestRow.status !== "pending") {
      return reply.code(409).send({ error: `request already ${requestRow.status}` });
    }

    const updated = await queryOne(
      `UPDATE rate_limit_increase_requests
          SET status         = $1,
              admin_response = $2,
              resolved_by    = $3,
              resolved_at    = now()
        WHERE id = $4
        RETURNING id, user_id, status, requested_tier, admin_response,
                  resolved_at`,
      [parsed.data.status, parsed.data.admin_response, actingAdmin.id, id],
    );

    if (parsed.data.status === "approved" && parsed.data.apply_tier) {
      await query(
        `UPDATE users SET rate_limit_tier = $1 WHERE id = $2`,
        [parsed.data.apply_tier, requestRow.user_id],
      );
    }

    return updated;
  });

  // ── Reports (premium, phase 1b) ────────────────────────────────
  // Operator triage surface for the /reports flow. Read-only listing
  // and detail; refund flips the hold (or grants a compensating
  // admin_credit row if the hold has already committed). Bug reports
  // queue lives here too.

  const reportsListQuery = z.object({
    status: z.enum(["queued", "running", "succeeded", "failed", "cancelled", "refunded"]).optional(),
    q: z.string().trim().optional(),
    limit: z.coerce.number().int().min(1).max(200).default(50),
  });

  app.get("/reports", async (req, reply) => {
    const parsed = reportsListQuery.safeParse(req.query);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const { status, q, limit } = parsed.data;
    const conds: string[] = [];
    const params: (string | number | boolean | null | string[])[] = [];
    if (status) {
      params.push(status);
      conds.push(`rj.status = $${params.length}`);
    }
    if (q) {
      params.push(`%${q}%`);
      conds.push(`(u.email ILIKE $${params.length} OR rj.query ILIKE $${params.length})`);
    }
    params.push(limit);
    const where = conds.length ? `WHERE ${conds.join(" AND ")}` : "";
    return await query(
      `SELECT rj.id, rj.user_id, u.email AS user_email,
              rj.politician_id, p.name AS politician_name,
              rj.query, rj.status, rj.estimated_credits, rj.chunk_count_actual,
              rj.model_used, rj.tokens_in, rj.tokens_out,
              rj.created_at, rj.finished_at, rj.error,
              rj.hold_ledger_id
         FROM report_jobs rj
         JOIN users u       ON u.id = rj.user_id
         JOIN politicians p ON p.id = rj.politician_id
         ${where}
        ORDER BY rj.created_at DESC
        LIMIT $${params.length}`,
      params,
    );
  });

  app.get<{ Params: { id: string } }>("/reports/:id", async (req, reply) => {
    const id = req.params.id;
    const row = await queryOne(
      `SELECT rj.*, u.email AS user_email, p.name AS politician_name
         FROM report_jobs rj
         JOIN users u       ON u.id = rj.user_id
         JOIN politicians p ON p.id = rj.politician_id
        WHERE rj.id = $1`,
      [id],
    );
    if (!row) return reply.notFound();
    return row;
  });

  const refundBody = z.object({
    reason: z.string().trim().min(3).max(500),
  });

  // Refund a report:
  //   - If the hold is still 'held', flip it to 'refunded' and mark the
  //     job 'refunded' (releaseHold path).
  //   - If the hold is already 'committed' (worker succeeded then user
  //     reports a problem), insert a compensating 'admin_credit' row
  //     for the same amount — never un-commit a state-flipped row.
  app.post<{ Params: { id: string }; Body: { reason: string } }>(
    "/reports/:id/refund",
    async (req, reply) => {
      const id = req.params.id;
      const parsed = refundBody.safeParse(req.body);
      if (!parsed.success) return reply.badRequest(parsed.error.message);

      const job = await queryOne<{
        id: string;
        user_id: string;
        status: string;
        estimated_credits: number;
        hold_ledger_id: string | null;
      }>(
        `SELECT id, user_id, status, estimated_credits, hold_ledger_id
           FROM report_jobs
          WHERE id = $1`,
        [id],
      );
      if (!job) return reply.notFound();

      const ledger = job.hold_ledger_id
        ? await queryOne<{ state: string }>(
            `SELECT state FROM credit_ledger WHERE id = $1`,
            [job.hold_ledger_id],
          )
        : null;

      const actingAdmin = await queryOne<{ id: string }>(
        `SELECT id FROM users WHERE email = $1 LIMIT 1`,
        [getAdminEmail(req)],
      );
      if (!actingAdmin) {
        return reply.code(500).send({ error: "acting admin not resolvable" });
      }

      if (ledger && ledger.state === "held") {
        // releaseHold path: flip held → refunded.
        await query(
          `UPDATE credit_ledger
              SET state = 'refunded',
                  reason = $2
            WHERE id = $1
              AND state = 'held'
              AND kind = 'report_hold'`,
          [job.hold_ledger_id, `admin refund: ${parsed.data.reason}`],
        );
        await query(
          `UPDATE report_jobs SET status = 'refunded' WHERE id = $1`,
          [id],
        );
        return { refunded: true, mode: "released_hold", credits: job.estimated_credits };
      }

      // Committed (or no hold): compensating admin_credit grant.
      await query(
        `INSERT INTO credit_ledger
             (user_id, delta, state, kind, reason, created_by_admin_id)
           VALUES ($1, $2, 'committed', 'admin_credit', $3, $4)`,
        [
          job.user_id,
          job.estimated_credits,
          `Compensating refund for report ${id}: ${parsed.data.reason}`,
          actingAdmin.id,
        ],
      );
      await query(
        `UPDATE report_jobs SET status = 'refunded' WHERE id = $1`,
        [id],
      );
      return { refunded: true, mode: "compensating_admin_credit", credits: job.estimated_credits };
    },
  );

  // ── Bug reports ────────────────────────────────────────────────
  const bugListQuery = z.object({
    status: z.enum(["open", "reviewing", "resolved", "dismissed"]).optional(),
    limit: z.coerce.number().int().min(1).max(200).default(50),
  });

  app.get("/bug-reports", async (req, reply) => {
    const parsed = bugListQuery.safeParse(req.query);
    if (!parsed.success) return reply.badRequest(parsed.error.message);
    const params: (string | number | boolean | null | string[])[] = [];
    let where = "";
    if (parsed.data.status) {
      params.push(parsed.data.status);
      where = `WHERE br.status = $${params.length}`;
    }
    params.push(parsed.data.limit);
    return await query(
      `SELECT br.id, br.report_id, br.user_id, u.email AS user_email,
              rj.politician_id, p.name AS politician_name, rj.query AS report_query,
              br.message, br.status, br.admin_notes, br.created_at, br.resolved_at
         FROM report_bug_reports br
         JOIN users u       ON u.id = br.user_id
         JOIN report_jobs rj ON rj.id = br.report_id
         JOIN politicians p ON p.id = rj.politician_id
         ${where}
        ORDER BY br.created_at DESC
        LIMIT $${params.length}`,
      params,
    );
  });

  const bugPatchBody = z.object({
    status: z.enum(["open", "reviewing", "resolved", "dismissed"]),
    admin_notes: z.string().trim().max(2000).nullable().optional(),
  });

  app.patch<{ Params: { id: string }; Body: { status: string; admin_notes?: string | null } }>(
    "/bug-reports/:id",
    async (req, reply) => {
      const id = req.params.id;
      const parsed = bugPatchBody.safeParse(req.body);
      if (!parsed.success) return reply.badRequest(parsed.error.message);
      const resolvedExpr =
        parsed.data.status === "resolved" || parsed.data.status === "dismissed"
          ? "now()"
          : "NULL";
      const updated = await queryOne(
        `UPDATE report_bug_reports
            SET status = $1,
                admin_notes = $2,
                resolved_at = ${resolvedExpr}
          WHERE id = $3
          RETURNING id, status, admin_notes, resolved_at`,
        [parsed.data.status, parsed.data.admin_notes ?? null, id],
      );
      if (!updated) return reply.notFound();
      return updated;
    },
  );
}
