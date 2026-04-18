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
        "description": "Fill speech_chunks.embedding via the local BGE-M3 service.",
        "cli": "embed-speech-chunks",
        "category": "hansard",
        "args": [
            {"name": "limit", "type": "int", "required": False,
             "help": "Max chunks to embed this run."},
            {"name": "batch_size", "type": "int", "required": False, "default": 32,
             "help": "Texts per /embed call. 32 is the default sweet spot; GPU hosts can push to 128."},
        ],
    },
    "embed-speech-chunks-next": {
        "description": "Fill speech_chunks.embedding_next via TEI (Qwen3-Embedding-0.6B). Faster path; requires `tei` service up (docker compose --profile embedding-qwen3 up -d tei).",
        "cli": "embed-speech-chunks-next",
        "category": "hansard",
        "args": [
            {"name": "limit", "type": "int", "required": False,
             "help": "Max chunks to embed this run."},
            {"name": "batch_size", "type": "int", "required": False, "default": 32,
             "help": "Texts per TEI /embed call. Match TEI's --max-client-batch-size (default 64)."},
        ],
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
