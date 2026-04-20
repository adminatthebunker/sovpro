"""Admin-panel command whitelist.

Single source of truth for which Click subcommands the admin UI can
enqueue. Keeping this as a Python dict (not a DB table) means:

- The UI form's typed inputs come straight from this module (served
  verbatim by the API's `GET /api/v1/admin/commands`).
- Changing the catalog is a code change with a diff — no migration,
  no out-of-band state to keep in sync.
- Unlisted commands never run via the admin path, even if a caller
  submits one by name. Internal scanner commands stay internal.

## Schema

Each entry is:

    "<command_key>": {
        "description": "Human-readable one-liner for the UI.",
        "cli":         "ingest-federal-hansard",     # the real Click name
        "category":    "hansard" | "bills" | "enrichment" | "maintenance",
        "args":        [
            {
                "name": "parliament",
                "type": "int" | "str" | "date" | "bool",
                "required": bool,
                "default": Optional,
                "help": "..."
            },
            ...
        ],
    }

`command_key` is typically identical to `cli` — but the split exists so
we could route a single UI command to a different internal name if it
ever makes sense.
"""
from __future__ import annotations

from typing import Any


COMMANDS: dict[str, dict[str, Any]] = {
    # ── Federal Hansard (semantic-layer pipeline) ─────────────────────
    "ingest-federal-hansard": {
        "description": "Pull federal House of Commons speeches from openparliament.ca into the `speeches` table.",
        "cli": "ingest-federal-hansard",
        "category": "hansard",
        "args": [
            {"name": "parliament", "type": "int", "required": True,
             "help": "Parliament number (e.g. 44)."},
            {"name": "session", "type": "int", "required": True,
             "help": "Session within the parliament (e.g. 1)."},
            {"name": "since", "type": "date", "required": False,
             "help": "Only fetch debates on/after this date (ISO YYYY-MM-DD)."},
            {"name": "until", "type": "date", "required": False,
             "help": "Only fetch debates on/before this date (ISO YYYY-MM-DD)."},
            {"name": "limit_debates", "type": "int", "required": False,
             "help": "Cap on sitting days fetched this run."},
            {"name": "limit_speeches", "type": "int", "required": False,
             "help": "Cap on TOTAL speeches ingested this run."},
        ],
    },
    "ingest-ab-hansard": {
        "description": "Pull Alberta Legislative Assembly speeches from PDF-only Hansard into the `speeches` table.",
        "cli": "ingest-ab-hansard",
        "category": "hansard",
        "args": [
            {"name": "legislature", "type": "int", "required": True,
             "help": "AB Legislature number (e.g. 31)."},
            {"name": "session", "type": "int", "required": True,
             "help": "Session within the legislature (e.g. 2)."},
            {"name": "since", "type": "date", "required": False,
             "help": "Only fetch sittings on/after this date (ISO YYYY-MM-DD)."},
            {"name": "until", "type": "date", "required": False,
             "help": "Only fetch sittings on/before this date (ISO YYYY-MM-DD)."},
            {"name": "limit_sittings", "type": "int", "required": False,
             "help": "Cap on sitting PDFs fetched this run (newest-first)."},
            {"name": "limit_speeches", "type": "int", "required": False,
             "help": "Cap on TOTAL speeches ingested this run."},
        ],
    },
    "ingest-bc-hansard": {
        "description": "Pull BC Legislative Assembly Hansard (Blues + Final HTML via LIMS HDMS) into `speeches`.",
        "cli": "ingest-bc-hansard",
        "category": "hansard",
        "args": [
            {"name": "parliament", "type": "int", "required": True,
             "help": "BC Parliament number (e.g. 43)."},
            {"name": "session", "type": "int", "required": True,
             "help": "Session within the parliament (e.g. 2)."},
            {"name": "since", "type": "date", "required": False,
             "help": "Only fetch sittings on/after this date (ISO YYYY-MM-DD)."},
            {"name": "until", "type": "date", "required": False,
             "help": "Only fetch sittings on/before this date (ISO YYYY-MM-DD)."},
            {"name": "limit_sittings", "type": "int", "required": False,
             "help": "Cap on sittings processed this run (newest-first when capped)."},
            {"name": "limit_speeches", "type": "int", "required": False,
             "help": "Cap on TOTAL speeches ingested this run."},
        ],
    },
    "resolve-bc-speakers": {
        "description": "Re-resolve politician_id on BC speeches with NULL politician_id.",
        "cli": "resolve-bc-speakers",
        "category": "hansard",
        "args": [
            {"name": "limit", "type": "int", "required": False,
             "help": "Cap speeches scanned (smoke-test aid)."},
        ],
    },
    "ingest-qc-hansard": {
        "description": "Pull Quebec Journal des débats (HTML) into `speeches`. Bilingual source, French primary.",
        "cli": "ingest-qc-hansard",
        "category": "hansard",
        "args": [
            {"name": "parliament", "type": "int", "required": True,
             "help": "QC parliament (législature) number (e.g. 43)."},
            {"name": "session", "type": "int", "required": True,
             "help": "Session within the parliament (e.g. 2)."},
            {"name": "since", "type": "date", "required": False,
             "help": "Only fetch sittings on/after this date (ISO YYYY-MM-DD)."},
            {"name": "until", "type": "date", "required": False,
             "help": "Only fetch sittings on/before this date (ISO YYYY-MM-DD)."},
            {"name": "limit_sittings", "type": "int", "required": False,
             "help": "Cap on sittings processed this run (newest-first when capped)."},
            {"name": "limit_speeches", "type": "int", "required": False,
             "help": "Cap on TOTAL speeches ingested this run."},
        ],
    },
    "resolve-qc-speakers": {
        "description": "Re-resolve politician_id on QC speeches with NULL politician_id.",
        "cli": "resolve-qc-speakers",
        "category": "hansard",
        "args": [
            {"name": "limit", "type": "int", "required": False,
             "help": "Cap speeches scanned (smoke-test aid)."},
        ],
    },
    "chunk-speeches": {
        "description": "Split speeches.text into retrievable `speech_chunks` rows (idempotent).",
        "cli": "chunk-speeches",
        "category": "hansard",
        "args": [
            {"name": "limit", "type": "int", "required": False,
             "help": "Max speeches to chunk this run (default: all pending)."},
        ],
    },
    "embed-speech-chunks": {
        "description": "Fill speech_chunks.embedding via TEI (Qwen3-Embedding-0.6B). ~50 chunks/sec end-to-end on GPU.",
        "cli": "embed-speech-chunks",
        "category": "hansard",
        "args": [
            {"name": "limit", "type": "int", "required": False,
             "help": "Max chunks to embed this run."},
            {"name": "batch_size", "type": "int", "required": False, "default": 32,
             "help": "Texts per TEI /embed call. Match TEI's --max-client-batch-size (default 64)."},
        ],
    },
    "resolve-acting-speakers": {
        "description": "Resolve politician_id on presiding-officer speeches (The Acting Speaker / Deputy Speaker + parenthesised MP name).",
        "cli": "resolve-acting-speakers",
        "category": "hansard",
        "args": [
            {"name": "limit", "type": "int", "required": False,
             "help": "Cap candidate speeches scanned (smoke-test aid)."},
        ],
    },
    "resolve-presiding-speakers": {
        "description": "Tie 'The Speaker' speeches to the sitting Speaker by date. Seeds politicians + politician_terms for the jurisdiction's Speaker roster, then updates NULL-politician_id rows. Province defaults to AB; pass BC to run for British Columbia.",
        "cli": "resolve-presiding-speakers",
        "category": "hansard",
        "args": [
            {"name": "province", "type": "enum", "required": False, "default": "AB",
             "choices": ["AB", "BC", "QC"],
             "help": "Which province's Speaker roster to resolve."},
            {"name": "limit", "type": "int", "required": False,
             "help": "Cap candidate speeches scanned (smoke-test aid)."},
        ],
    },
    "refresh-coverage-stats": {
        "description": "Recompute jurisdiction_sources counts (speeches, politicians, bills) and flip Hansard status from live data. Drives /coverage.",
        "cli": "refresh-coverage-stats",
        "category": "admin",
        "args": [],
    },

    # ── Provincial bills (already live) ───────────────────────────────
    "ingest-ns-bills": {
        "description": "Nova Scotia bills via Socrata.",
        "cli": "ingest-ns-bills", "category": "bills",
        "args": [{"name": "limit", "type": "int", "required": False, "help": "Max bills this run."}],
    },
    "ingest-ns-bills-rss": {
        "description": "Nova Scotia current-session RSS refresh (status + events).",
        "cli": "ingest-ns-bills-rss", "category": "bills", "args": [],
    },
    "ingest-on-bills": {
        "description": "Ontario P44-S1 bills via ola.org.",
        "cli": "discover-on-bills", "category": "bills",
        "args": [
            {"name": "parliament", "type": "int", "required": False, "default": 44,
             "help": "Parliament number."},
            {"name": "session", "type": "int", "required": False, "default": 1,
             "help": "Session number."},
        ],
    },
    "ingest-bc-bills": {
        "description": "British Columbia bills via LIMS JSON endpoint.",
        "cli": "ingest-bc-bills", "category": "bills",
        "args": [
            {"name": "parliament", "type": "int", "required": False, "help": "Parliament number."},
            {"name": "session", "type": "int", "required": False, "help": "Session number."},
        ],
    },
    "ingest-qc-bills": {
        "description": "Quebec bills via donneesquebec CSV.",
        "cli": "ingest-qc-bills", "category": "bills", "args": [],
    },
    "ingest-qc-bills-rss": {
        "description": "Quebec current-session RSS refresh.",
        "cli": "ingest-qc-bills-rss", "category": "bills", "args": [],
    },
    "ingest-ab-bills": {
        "description": "Alberta bills via Assembly Dashboard. Default current session; --all-sessions backfills Legislature 1+ (~137 sessions).",
        "cli": "ingest-ab-bills", "category": "bills",
        "args": [
            {"name": "legislature", "type": "int", "required": False, "help": "One specific legislature (pair with --session)."},
            {"name": "session", "type": "int", "required": False, "help": "One specific session (requires --legislature)."},
            {"name": "all_sessions_in_legislature", "type": "int", "required": False, "help": "Every session within legislature L."},
            {"name": "all_sessions", "type": "bool", "required": False, "help": "Full historical backfill (Legislature 1+, ~137 sessions × 1.5s delay ≈ 3.5 min)."},
            {"name": "delay", "type": "int", "required": False, "default": 2, "help": "Seconds between session fetches (be polite)."},
        ],
    },
    "ingest-nb-bills": {
        "description": "New Brunswick bills via legnb.ca.",
        "cli": "ingest-nb-bills", "category": "bills",
        "args": [
            {"name": "legislature", "type": "int", "required": False, "help": "Legislature number."},
            {"name": "session", "type": "int", "required": False, "help": "Session number."},
        ],
    },
    "ingest-nl-bills": {
        "description": "Newfoundland & Labrador bills via assembly.nl.ca (GA index).",
        "cli": "ingest-nl-bills", "category": "bills",
        "args": [
            {"name": "ga", "type": "int", "required": False, "help": "General Assembly number (pair with --session)."},
            {"name": "session", "type": "int", "required": False, "help": "Session number (requires --ga)."},
            {"name": "all_sessions_in_ga", "type": "int", "required": False, "help": "Every session in GA G."},
            {"name": "all_sessions", "type": "bool", "required": False, "help": "Every session in the index (GA 44+, ~40 sessions)."},
        ],
    },
    "ingest-nt-bills": {
        "description": "Northwest Territories bills via ntassembly.ca (consensus gov't, no sponsors).",
        "cli": "ingest-nt-bills", "category": "bills",
        "args": [
            {"name": "delay", "type": "int", "required": False, "default": 2,
             "help": "Seconds between per-bill detail-page fetches (be polite)."},
        ],
    },
    "ingest-nu-bills": {
        "description": "Nunavut bills via assembly.nu.ca (consensus gov't, no sponsors; multilingual).",
        "cli": "ingest-nu-bills", "category": "bills",
        "args": [
            {"name": "assembly", "type": "int", "required": False, "help": "Assembly number (default: current)."},
            {"name": "session", "type": "int", "required": False, "help": "Session number (default: current)."},
        ],
    },

    # ── Reps / rosters (Open North) ──────────────────────────────────
    "ingest-mps": {
        "description": "Federal MPs roster from Open North Represent.",
        "cli": "ingest-mps", "category": "enrichment", "args": [],
    },
    "ingest-senators": {
        "description": "Canadian Senate roster from sencanada.ca.",
        "cli": "ingest-senators", "category": "enrichment", "args": [],
    },
    "ingest-mlas": {
        "description": "Provincial/territorial legislators via Open North.",
        "cli": "ingest-mlas", "category": "enrichment", "args": [],
    },
    "ingest-councils": {
        "description": "Municipal councillors via Open North.",
        "cli": "ingest-councils", "category": "enrichment", "args": [],
    },
    "ingest-legislatures": {
        "description": "Full provincial/territorial legislature ingest.",
        "cli": "ingest-legislatures", "category": "enrichment", "args": [],
    },
    "harvest-personal-socials": {
        "description": "Scrape politicians' personal sites for social handles.",
        "cli": "harvest-personal-socials", "category": "enrichment",
        "args": [{"name": "limit", "type": "int", "required": False,
                  "help": "Max politicians to process this run."}],
    },

    # ── Socials audit + backfill (tiered) ────────────────────────────
    # Tier 1 (zero LLM): re-run the existing upstream enrichers
    # (enrich-socials-all, harvest-personal-socials) first. Tier 2 is
    # pattern probing; Tier 3 is Sonnet-agent web search. Run audit-
    # socials between tiers to snapshot progress.
    "audit-socials": {
        "description": "Snapshot social-media coverage; refresh v_socials_missing view.",
        "cli": "audit-socials", "category": "enrichment",
        "args": [
            {"name": "no_csv", "type": "bool", "required": False,
             "help": "Skip CSV export; just print summary tables."},
        ],
    },
    "probe-missing-socials": {
        "description": "Tier-2: pattern-probe candidate URLs for missing socials. Zero LLM cost.",
        "cli": "probe-missing-socials", "category": "enrichment",
        "args": [
            {"name": "platform", "type": "str", "required": False, "default": "bluesky",
             "help": "One of: bluesky, twitter, facebook, instagram, youtube, threads."},
            {"name": "limit", "type": "int", "required": False, "default": 500,
             "help": "Max missing-rows to probe this run."},
            {"name": "dry_run", "type": "bool", "required": False,
             "help": "Print would-be inserts without writing."},
        ],
    },
    "agent-missing-socials": {
        "description": "Tier-3: Sonnet agent + web_search fills residual missing handles. Requires ANTHROPIC_API_KEY.",
        "cli": "agent-missing-socials", "category": "enrichment",
        "args": [
            {"name": "platform", "type": "str", "required": False,
             "help": "Focus on a single platform (omit for all-missing-per-politician)."},
            {"name": "batch_size", "type": "int", "required": False, "default": 10,
             "help": "Politicians per agent call (max 25)."},
            {"name": "max_batches", "type": "int", "required": False, "default": 20,
             "help": "Hard cap on agent calls per run."},
            {"name": "model", "type": "str", "required": False,
             "help": "Override the default Claude model."},
            {"name": "dry_run", "type": "bool", "required": False,
             "help": "Print candidate hits without inserting."},
        ],
    },
    "verify-socials": {
        "description": "Liveness check on politician_socials URLs. Writes social_dead change rows on live→dead flips.",
        "cli": "verify-socials", "category": "enrichment",
        "args": [
            {"name": "limit", "type": "int", "required": False, "default": 500,
             "help": "Max rows to verify per run."},
            {"name": "stale_hours", "type": "int", "required": False, "default": 168,
             "help": "Re-verify rows whose last_verified_at is older than this."},
        ],
    },
    "enrich-socials-all": {
        "description": "Tier-1: Run wikidata → openparliament → masto-host enrichment end-to-end.",
        "cli": "enrich-socials-all", "category": "enrichment", "args": [],
    },

    # ── Maintenance ──────────────────────────────────────────────────
    "refresh-views": {
        "description": "Refresh `map_politicians` and `map_organizations` materialized views.",
        "cli": "refresh-views", "category": "maintenance", "args": [],
    },
    "seed-orgs": {
        "description": "Re-apply the referendum/advocacy organizations seed.",
        "cli": "seed-orgs", "category": "maintenance", "args": [],
    },
    "backfill-terms": {
        "description": "One-time: open an initial politician_terms row for every active politician without an existing open term. Prereq for party-at-time queries.",
        "cli": "backfill-terms", "category": "maintenance", "args": [],
    },
    "backfill-politician-photos": {
        "description": "Mirror upstream politician portraits to the local /assets volume; re-fetch stale rows (>30 days) on each run. Idempotent.",
        "cli": "backfill-politician-photos", "category": "maintenance",
        "args": [
            {"name": "limit", "type": "int", "required": False,
             "help": "Cap politicians processed this run."},
            {"name": "stale_days", "type": "int", "required": False, "default": 30,
             "help": "Re-fetch if last fetch is older than N days."},
            {"name": "politician_id", "type": "str", "required": False,
             "help": "Process a single politician by UUID (overrides other filters)."},
            {"name": "concurrency", "type": "int", "required": False, "default": 4,
             "help": "Parallel fetches. Per-host spacing still applies."},
        ],
    },
    "scan": {
        "description": "Infrastructure scan across every tracked website.",
        "cli": "scan", "category": "maintenance",
        "args": [
            {"name": "limit", "type": "int", "required": False,
             "help": "Max sites this run."},
            {"name": "stale_hours", "type": "int", "required": False, "default": 6,
             "help": "Re-scan sites whose last scan is older than this many hours."},
        ],
    },
}


def list_commands() -> list[dict[str, Any]]:
    """Return the catalog as a JSON-friendly array (sorted by category → key)."""
    out = []
    for key, meta in COMMANDS.items():
        out.append({"key": key, **meta})
    out.sort(key=lambda c: (c["category"], c["key"]))
    return out


def get_command(key: str) -> dict[str, Any] | None:
    return COMMANDS.get(key)


def build_cli_args(key: str, args: dict[str, Any]) -> list[str]:
    """Translate a (command_key, {arg_name: value}) pair into a list suitable
    for `subprocess.run`. Validates against the schema and raises ValueError
    on unknown command or bad arg types.

    Click's convention is `--name value` with underscores replaced by dashes
    in the flag name. Boolean args become `--name` when true, omitted when
    false.
    """
    meta = COMMANDS.get(key)
    if meta is None:
        raise ValueError(f"unknown command: {key}")
    schema = {a["name"]: a for a in meta["args"]}

    cli_tokens: list[str] = [meta["cli"]]

    # Enforce required args
    for arg in meta["args"]:
        if arg.get("required") and arg["name"] not in args:
            raise ValueError(f"missing required arg: {arg['name']}")

    # Translate
    for name, value in args.items():
        if name not in schema:
            raise ValueError(f"unknown arg for {key}: {name}")
        if value is None:
            continue
        spec = schema[name]
        flag = f"--{name.replace('_', '-')}"
        t = spec.get("type", "str")
        if t == "bool":
            if bool(value):
                cli_tokens.append(flag)
        else:
            cli_tokens.extend([flag, str(value)])
    return cli_tokens
