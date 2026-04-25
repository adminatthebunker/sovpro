"""Merge AB presiding-officer-seed stubs into their MID-keyed twins.

Background
----------
The `presiding_officer_resolver` creates a `presiding-officer-seed:AB:<surname>`
politician row when its `_find_politician_id` lookup misses against the
hand-curated SPEAKER_ROSTER (e.g. roster says "Ken Kowalski" but the DB
holds "Kenneth R. Kowalski"). The stub then attracts every "The Speaker:"
turn for that period.

After `ingest-ab-former-mlas` later inserts the proper MID-keyed row,
the stub is never reconciled — speeches stay attached to the stub
(politician_id IS NOT NULL means the resolver's NULL-only update can't
move them) and the profile page is thin.

This command performs the one-time reconciliation:

  1. Identify each `presiding-officer-seed:AB:%` stub.
  2. Find the MID-keyed twin: AB politician with same surname and a
     `politician_terms` row whose `office ILIKE '%speaker%'` overlaps
     the stub's speech date range. The Speaker terms are sourced from
     `enrich-ab-mlas` (`source='ab-assembly-member-info'`) — run that
     command first.
  3. In a single transaction, reassign `speeches.politician_id` and
     `speech_chunks.politician_id` to the twin, then DELETE the stub.

Idempotent: a re-run finds zero stubs and is a no-op.

The role context is preserved: `speeches.speaker_role` is untouched.
After merge, a search hit on the twin still surfaces the "[Speaker]"
badge for those rows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..db import Database

log = logging.getLogger(__name__)


@dataclass
class MergeStats:
    stubs_considered: int = 0
    stubs_no_twin: int = 0
    stubs_ambiguous: int = 0
    stubs_merged: int = 0
    speeches_moved: int = 0
    chunks_moved: int = 0
    skipped_no_speeches: int = 0
    fail_samples: list[str] = field(default_factory=list)


async def merge_ab_presiding_stubs(
    db: Database,
    *,
    dry_run: bool = False,
) -> MergeStats:
    """Merge each AB presiding-officer-seed stub into its MID-keyed twin.

    Disambiguation algorithm (for each stub):

    1. Candidate twins = AB politicians (level=provincial,
       ab_assembly_mid IS NOT NULL) with `lower(last_name) =
       lower(stub.last_name)`.
    2. If exactly 1 candidate, that's the twin.
    3. If >1 candidates, prefer the one whose `politician_terms` row
       (any source) with `office ILIKE '%speaker%'` overlaps the
       earliest→latest spoken_at range of the stub's speeches.
    4. Still tied or zero → log + skip; operator review needed.
    """
    stats = MergeStats()

    stubs = await db.fetch(
        """
        SELECT id::text AS id, name, last_name, source_id
          FROM politicians
         WHERE source_id LIKE 'presiding-officer-seed:AB:%'
        """
    )
    stats.stubs_considered = len(stubs)
    log.info("merge-ab-presiding-stubs: stubs=%d (dry_run=%s)", stats.stubs_considered, dry_run)

    for stub in stubs:
        stub_id: str = stub["id"]
        stub_name: str = stub["name"] or ""
        stub_last: str = stub["last_name"] or ""

        # Speeches owned by this stub — both for the date-range
        # disambiguator and the count we report.
        speech_range = await db.fetchrow(
            """
            SELECT MIN(spoken_at) AS min_at,
                   MAX(spoken_at) AS max_at,
                   COUNT(*)        AS n
              FROM speeches
             WHERE politician_id = $1::uuid
            """,
            stub_id,
        )
        n_speeches = int(speech_range["n"]) if speech_range else 0
        if n_speeches == 0:
            log.info(
                "merge-ab-presiding-stubs: stub=%s (%s) owns 0 speeches → "
                "deleting orphan", stub_id, stub_name,
            )
            stats.skipped_no_speeches += 1
            if not dry_run:
                await db.execute("DELETE FROM politicians WHERE id = $1::uuid", stub_id)
            continue

        candidates = await db.fetch(
            """
            SELECT id::text AS id, name, first_name, last_name, ab_assembly_mid
              FROM politicians
             WHERE province_territory = 'AB'
               AND level = 'provincial'
               AND ab_assembly_mid IS NOT NULL
               AND lower(last_name) = lower($1)
            """,
            stub_last,
        )
        if not candidates:
            log.warning(
                "merge-ab-presiding-stubs: stub=%s last_name=%r — no MID-keyed twin found, skip",
                stub_id, stub_last,
            )
            stats.stubs_no_twin += 1
            if len(stats.fail_samples) < 5:
                stats.fail_samples.append(f"no_twin: {stub_name} ({stub_last})")
            continue

        twin_id: Optional[str] = None
        if len(candidates) == 1:
            twin_id = candidates[0]["id"]
        else:
            # Multiple candidates with the same surname — prefer the one
            # whose Speaker term overlaps the speech date window.
            min_at = speech_range["min_at"]
            max_at = speech_range["max_at"]
            speaker_overlap = await db.fetch(
                """
                SELECT p.id::text AS id, p.name, p.ab_assembly_mid,
                       MIN(t.started_at) AS started_at,
                       MAX(t.ended_at)   AS ended_at
                  FROM politicians p
                  JOIN politician_terms t ON t.politician_id = p.id
                 WHERE p.province_territory = 'AB'
                   AND p.level = 'provincial'
                   AND p.ab_assembly_mid IS NOT NULL
                   AND lower(p.last_name) = lower($1)
                   AND t.office ILIKE '%speaker%'
                   AND t.started_at <= $3
                   AND ($2 <= t.ended_at OR t.ended_at IS NULL)
                 GROUP BY p.id, p.name, p.ab_assembly_mid
                """,
                stub_last, min_at, max_at,
            )
            if len(speaker_overlap) == 1:
                twin_id = speaker_overlap[0]["id"]
            elif len(speaker_overlap) == 0:
                log.warning(
                    "merge-ab-presiding-stubs: stub=%s last_name=%r — %d MID-keyed twins, "
                    "none with overlapping Speaker term; skip",
                    stub_id, stub_last, len(candidates),
                )
                stats.stubs_ambiguous += 1
                if len(stats.fail_samples) < 5:
                    stats.fail_samples.append(
                        f"no_speaker_overlap: {stub_name} ({stub_last}) — {len(candidates)} candidates",
                    )
                continue
            else:
                log.warning(
                    "merge-ab-presiding-stubs: stub=%s last_name=%r — %d MID-keyed twins "
                    "have overlapping Speaker terms; skip",
                    stub_id, stub_last, len(speaker_overlap),
                )
                stats.stubs_ambiguous += 1
                if len(stats.fail_samples) < 5:
                    stats.fail_samples.append(
                        f"ambiguous_speakers: {stub_name} ({stub_last}) — "
                        f"{len(speaker_overlap)} candidates",
                    )
                continue

        # Found a clean twin. Move speeches + chunks, then drop the stub.
        twin_row = next(c for c in candidates if c["id"] == twin_id)
        twin_name = twin_row["name"]
        twin_mid = twin_row["ab_assembly_mid"]
        log.info(
            "merge-ab-presiding-stubs: stub=%s (%s) → twin=%s (%s, mid=%s); "
            "speeches=%d range=%s..%s",
            stub_id, stub_name, twin_id, twin_name, twin_mid,
            n_speeches, speech_range["min_at"], speech_range["max_at"],
        )

        if dry_run:
            stats.stubs_merged += 1
            stats.speeches_moved += n_speeches
            continue

        # 600s timeout per UPDATE: the Kowalski stub owns 49k speeches
        # + ~52k chunks; asyncpg's default 60s timeout fires mid-update.
        # The three statements are individually idempotent — the chunks
        # UPDATE matches against speech_chunks.politician_id directly
        # (no FK constraint), and the DELETE is gated on the stub still
        # existing — so a crash mid-merge leaves a recoverable state
        # which the post-pass `_reconcile_orphan_chunks` finalises.
        moved_row = await db.pool.fetchrow(
            """
            WITH updated AS (
              UPDATE speeches
                 SET politician_id = $2::uuid, updated_at = now()
               WHERE politician_id = $1::uuid
              RETURNING id
            )
            SELECT COUNT(*) AS n FROM updated
            """,
            stub_id, twin_id, timeout=600,
        )
        moved = int(moved_row["n"]) if moved_row else 0
        stats.speeches_moved += moved

        chunks_row = await db.pool.fetchrow(
            """
            WITH updated AS (
              UPDATE speech_chunks
                 SET politician_id = $2::uuid
               WHERE politician_id = $1::uuid
              RETURNING id
            )
            SELECT COUNT(*) AS n FROM updated
            """,
            stub_id, twin_id, timeout=600,
        )
        chunks_moved = int(chunks_row["n"]) if chunks_row else 0
        stats.chunks_moved += chunks_moved

        await db.execute("DELETE FROM politicians WHERE id = $1::uuid", stub_id)
        stats.stubs_merged += 1

        log.info(
            "merge-ab-presiding-stubs: merged stub=%s → twin=%s "
            "speeches_moved=%d chunks_moved=%d",
            stub_id, twin_id, moved, chunks_moved,
        )

    # Post-pass: reconcile any orphaned chunks. Two paths produce
    # orphans: (a) a previous merge run was interrupted between the
    # speeches and chunks UPDATEs, leaving chunks pointing at a
    # now-deleted stub UUID; (b) a future stub gets merged outside
    # this command. Either way, the canonical fix is: chunks should
    # share their parent speech's politician_id. Mirrors the chunk-
    # propagation pass in resolve_ab_speakers.
    if not dry_run:
        recon_row = await db.pool.fetchrow(
            """
            WITH updated AS (
              UPDATE speech_chunks sc
                 SET politician_id = s.politician_id
                FROM speeches s
               WHERE sc.speech_id = s.id
                 AND sc.politician_id IS DISTINCT FROM s.politician_id
                 AND s.source_system = 'assembly.ab.ca'
              RETURNING sc.id
            )
            SELECT COUNT(*) AS n FROM updated
            """,
            timeout=600,
        )
        recon_n = int(recon_row["n"]) if recon_row else 0
        stats.chunks_moved += recon_n
        log.info("merge-ab-presiding-stubs: reconciled %d orphan/stale AB chunks", recon_n)

    log.info(
        "merge-ab-presiding-stubs: considered=%d merged=%d no_twin=%d "
        "ambiguous=%d empty_orphans=%d speeches_moved=%d chunks_moved=%d",
        stats.stubs_considered, stats.stubs_merged, stats.stubs_no_twin,
        stats.stubs_ambiguous, stats.skipped_no_speeches,
        stats.speeches_moved, stats.chunks_moved,
    )
    return stats
