"""Draft V1 eval query set with auto-labeled ground truth.

Writes services/embed/eval/queries/queries.jsonl — 40 queries across the
six categories A–F from docs/archive/embedding-eval-2026-04.md. Ground
truth is the top-20 chunks per query by Postgres full-text `ts_rank`
against a hand-picked keyword expansion. This is a V1 "low-quality
labels that we can beat with embedding models" baseline, per the
tracking doc's decision to run Path 3 (fastest-to-first-results).

Heuristics:

- For Category A/D/E/F, filter candidates to the same language as the
  query itself (English tsv_config for EN queries, etc.).
- For Category B (cross-lingual), filter to the *opposite* language
  from the query — this is the whole point of the test.
- For Category C (script detection), we take the full literal query
  text and match it against the same-language corpus; near-duplicates
  surface via high ts_rank.

The keyword list for each query is intentionally a *superset* of what
an embedding model should retrieve. If the FTS ranking has reasonable
recall on the canonical term, downstream eval measures whether the
embedding models can do better (especially on the euphemism + cross-
lingual categories, where FTS is known-weak).

Run:
    docker exec -i sw-scanner-jobs python - < services/embed/eval/scripts/draft_queries.py
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import asyncpg


# Output path *inside* the scanner container. We write to /tmp and then
# copy back to the host via `docker cp`.
OUT_PATH = Path("/tmp/queries.jsonl")


@dataclass
class Query:
    query_id: str
    category: str            # 'A_euphemism' | 'B_crosslingual' | 'C_script' | 'D_stance' | 'E_bill' | 'F_edge'
    language: str            # 'en' | 'fr' — the LANGUAGE OF THE QUERY TEXT
    query_text: str
    search_terms: str        # space-separated keyword expansion fed to plainto_tsquery
    target_language: str     # 'en' | 'fr' — language of the ANSWER space; differs from `language` only in category B
    notes: str = ""


QUERIES: list[Query] = [
    # ── Category A: euphemism-robust topic search (9 queries, 7 EN + 2 FR) ──
    Query("A01", "A_euphemism", "en", "speeches arguing for carbon pricing",
          "carbon pricing pollution price output-based emissions carbon tax climate", "en",
          "expect cross-party coverage incl. Conservative critique"),
    Query("A02", "A_euphemism", "en", "speeches discussing housing affordability",
          "housing affordability rentals supply gatekeepers missing middle crisis", "en",
          "topic spans supply-side, demand-side, renters, buyers"),
    Query("A03", "A_euphemism", "en", "speeches about Indigenous reconciliation",
          "reconciliation indigenous UNDRIP treaty TRC residential schools nation", "en",
          "includes TRC Calls to Action language"),
    Query("A04", "A_euphemism", "en", "speeches on foreign interference in elections",
          "foreign interference CSIS election integrity hostile state influence", "en",
          "Chinese interference inquiry era"),
    Query("A05", "A_euphemism", "en", "speeches on immigration and refugee policy",
          "immigration newcomers refugees asylum border migrants", "en",
          ""),
    Query("A06", "A_euphemism", "en", "speeches on health care funding and wait times",
          "health care CHT transfer wait times doctor shortage hospitals", "en",
          ""),
    Query("A07", "A_euphemism", "en", "speeches on interest rates and monetary policy",
          "interest rates Bank Canada monetary mortgage inflation", "en",
          ""),
    Query("A08", "A_euphemism", "fr", "discours sur la crise climatique",
          "climat carbone pollution transition énergétique fossiles", "fr",
          ""),
    Query("A09", "A_euphemism", "fr", "discours sur la langue française",
          "français langue officielle bilinguisme Québec", "fr",
          ""),

    # ── Category B: cross-lingual retrieval (8 queries, 4 EN→FR + 4 FR→EN) ──
    Query("B01", "B_crosslingual", "en", "supply management in dairy",
          "gestion offre produits laitiers lait agriculture", "fr",
          "EN query, expect FR results about gestion de l'offre"),
    Query("B02", "B_crosslingual", "en", "foreign interference",
          "ingérence étrangère CSIS élections influence", "fr",
          "EN query, expect FR coverage of ingérence"),
    Query("B03", "B_crosslingual", "en", "notwithstanding clause",
          "disposition dérogation charte droits libertés", "fr",
          "EN query, expect FR coverage of disposition de dérogation"),
    Query("B04", "B_crosslingual", "en", "carbon tax",
          "taxe carbone prix pollution climat", "fr",
          "EN query, expect FR rhetoric on taxe carbone"),
    Query("B05", "B_crosslingual", "fr", "soins de santé",
          "health care funding hospitals doctors wait times", "en",
          "FR query, expect EN health care speeches"),
    Query("B06", "B_crosslingual", "fr", "logement abordable",
          "affordable housing rentals supply crisis", "en",
          "FR query, expect EN affordable housing speeches"),
    Query("B07", "B_crosslingual", "fr", "réconciliation autochtone",
          "indigenous reconciliation UNDRIP treaty TRC", "en",
          "FR query, expect EN reconciliation speeches"),
    Query("B08", "B_crosslingual", "fr", "oléoduc",
          "pipeline Trans Mountain Keystone oil energy", "en",
          "FR query about pipelines, expect EN pipeline discussion"),

    # ── Category C: talking-points / script detection (5 queries, 4 EN + 1 FR) ──
    Query("C01", "C_script", "en", "inflation caused by government spending",
          "inflation government spending deficit Liberal", "en",
          "CPC standard talking point — expect near-duplicates"),
    Query("C02", "C_script", "en", "Trudeau's carbon tax is a tax on everything",
          "Trudeau carbon tax everything families struggling", "en",
          "Classic CPC script; read by multiple MPs verbatim"),
    Query("C03", "C_script", "en", "Canadians are struggling to make ends meet",
          "Canadians struggling ends meet paycheque affordability", "en",
          "Cross-party empathy opener"),
    Query("C04", "C_script", "en", "I rise today to speak to Bill",
          "rise today speak bill honour house", "en",
          "Procedural opener — very high base rate; test edge"),
    Query("C05", "C_script", "fr", "le gouvernement libéral n'a pas",
          "gouvernement libéral promesse échec", "fr",
          "BQ/CPC standard attack opener in French"),

    # ── Category D: stance matching (7 queries, 5 EN + 2 FR) ──
    Query("D01", "D_stance", "en", "speeches arguing against carbon pricing on economic grounds",
          "carbon tax economy jobs families cost against", "en",
          "pro-topic retrieval should LOSE to stance-aware retrieval"),
    Query("D02", "D_stance", "en", "speeches in favour of carbon pricing",
          "carbon pricing climate necessary effective support", "en",
          ""),
    Query("D03", "D_stance", "en", "speeches opposing Bill C-18 Online News Act",
          "C-18 online news act against opposition Meta Google", "en",
          ""),
    Query("D04", "D_stance", "en", "speeches supporting firearms legislation",
          "firearms gun control C-21 safety support", "en",
          ""),
    Query("D05", "D_stance", "en", "speeches criticizing vaccine mandates",
          "vaccine mandate passport freedom rights oppose", "en",
          ""),
    Query("D06", "D_stance", "fr", "discours contre la taxe carbone",
          "taxe carbone contre économie fardeau", "fr",
          ""),
    Query("D07", "D_stance", "fr", "discours pour l'action climatique",
          "action climatique nécessaire GES climat", "fr",
          ""),

    # ── Category E: bill-specific discussion (5 queries, 4 EN + 1 FR) ──
    Query("E01", "E_bill", "en", "Bill C-18 Online News Act provisions",
          "C-18 online news act journalism compensation platforms", "en",
          ""),
    Query("E02", "E_bill", "en", "Bill C-21 firearms legislation",
          "C-21 firearms handgun gun assault rifle", "en",
          ""),
    Query("E03", "E_bill", "en", "Bill C-11 online streaming regulation",
          "C-11 online streaming CRTC content Canadian", "en",
          ""),
    Query("E04", "E_bill", "en", "Bill C-63 online harms",
          "C-63 online harms safety hate speech", "en",
          ""),
    Query("E05", "E_bill", "fr", "projet de loi C-18 nouvelles en ligne",
          "C-18 nouvelles en ligne journalisme plateformes", "fr",
          ""),

    # ── Category F: edge cases (6 queries, 4 EN + 2 FR) ──
    Query("F01", "F_edge", "en", "inflation",
          "inflation", "en",
          "very short query — tests base retrieval"),
    Query("F02", "F_edge", "en", "point of order on Bill C-18",
          "point order Bill C-18 speaker rule", "en",
          "procedural vs substantive"),
    Query("F03", "F_edge", "en", "Pierre Poilievre on the Bank of Canada",
          "Poilievre Bank Canada governor monetary", "en",
          "single-speaker filter"),
    Query("F04", "F_edge", "en", "Speaker of the House ruling on privilege",
          "Speaker House ruling privilege member", "en",
          "procedural low-frequency"),
    Query("F05", "F_edge", "fr", "question de privilège",
          "question privilège président chambre", "fr",
          "procedural FR"),
    Query("F06", "F_edge", "fr", "Yukon",
          "Yukon territoire nord", "fr",
          "low-resource topic — small result set expected"),
]


SQL = """
SELECT id::text
  FROM speech_chunks
 WHERE language = $1
   AND tsv @@ to_tsquery($2::regconfig, $3)
 ORDER BY ts_rank(tsv, to_tsquery($2::regconfig, $3)) DESC,
          spoken_at DESC NULLS LAST
 LIMIT 20;
"""


def tsv_config_for(language: str) -> str:
    return "english" if language == "en" else "french"


def to_or_tsquery(search_terms: str) -> str:
    """Turn a space-separated keyword expansion into an OR-joined tsquery.

    `plainto_tsquery` AND-joins all terms, which on ~200-token chunks
    produces zero hits for anything beyond 2-3 terms. We want OR semantics
    so the ranking function does the work of surfacing the best matches.
    Single-word terms only (we pre-stripped punctuation in the query
    definitions); multi-word phrases become `word1<->word2` via the
    phrase operator.
    """
    parts: list[str] = []
    for raw in search_terms.split():
        # Drop characters to_tsquery treats as operators to keep it lenient.
        cleaned = raw.replace("'", "").replace(":", "").replace("&", "").replace("|", "").replace("!", "").replace("(", "").replace(")", "")
        if cleaned:
            parts.append(cleaned)
    return " | ".join(parts) if parts else ""


async def main() -> None:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    assert pool is not None

    written = 0
    zero_hit = 0

    with OUT_PATH.open("w") as fh:
        async with pool.acquire() as conn:
            for q in QUERIES:
                cfg = tsv_config_for(q.target_language)
                tsquery = to_or_tsquery(q.search_terms)
                rows = await conn.fetch(SQL, q.target_language, cfg, tsquery)
                chunk_ids = [r["id"] for r in rows]
                if not chunk_ids:
                    zero_hit += 1
                    print(f"  ⚠  {q.query_id} [{q.category}]: ZERO hits — keyword expansion too narrow?")

                record = {
                    "query_id": q.query_id,
                    "category": q.category,
                    "language": q.language,
                    "query_text": q.query_text,
                    "target_language": q.target_language,
                    "search_terms": q.search_terms,
                    "relevant_chunk_ids": chunk_ids,
                    "label_source": "auto_fts_top20",
                    "notes": q.notes,
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
                print(f"  {q.query_id} [{q.category:16s}] {q.language}->{q.target_language}  "
                      f"{len(chunk_ids):>2d} hits  — {q.query_text[:60]}")

    await pool.close()
    print(f"\nWrote {written} queries to {OUT_PATH} ({zero_hit} with zero FTS hits).")


if __name__ == "__main__":
    asyncio.run(main())
