# Reply to Open North (M.)

Draft reply to M. at Open North after they wrote back to the intro email asking to set up a call about Represent usage.

Tone: casual, appreciative, concrete. They explicitly said "we know we need to invest in Represent but have struggled to find the resources" — the goal of the reply is to give them enough specifics that the call is immediately useful (i.e. "here's where investment would move the needle") rather than a generic hello.

Numbers are from the production DB as of 2026-04-21 — refresh before sending if significant time has passed (see appendix).

---

Hey M.,

Thrilled to get a response — I know you run a small outfit so the reply is genuinely appreciated.

Quick context for the call so we can skip the preamble:

**How I'm using Represent today**

Two integration points in the stack:

1. *Batch ingest* — paging `/representatives/{set}/` for the House of Commons, all 13 provincial/territorial legislatures, and ~80 municipal councils (pulled dynamically from `/representative-sets/?limit=1000`). That's ~1,786 politicians in my DB sourced from Represent right now — 343 federal + 767 provincial + 676 municipal. For each rep I persist name, party, riding, email, photo_url, personal_url, the `offices` array (normalized into a per-office table), and `extra.urls` (parsed into a socials seed for Twitter / Facebook / Instagram / YouTube / TikTok / LinkedIn).

2. *Boundaries into PostGIS* — `/boundaries/{set}/{slug}/simple_shape` for every unique riding, loaded as MultiPolygon with a pre-simplified copy for the map viewer. 1,454 polygons currently.

3. *Live postcode proxy* — `/api/v1/lookup/postcode/:code` on my site hits `represent.opennorth.ca/postcodes/{code}/?format=json` in real-time and enriches each returned rep with my local hosting-sovereignty data. 10-min edge cache.

On top of that every full ingest runs through a diff pass that populates an audit trail, opens/closes term records, and detects retirements when a `source_id` disappears from a full-set fetch. Represent is basically the backbone of "who represented whom, when" in my data model.

**What else I'm pairing it with**

- *openparliament.ca* — deeper federal Hansard + votes than Represent offers
- *Per-province Hansard* — different adventure per province. XML for QC, HTML scrapes for AB / BC / MB, iframe-backed content servers elsewhere. ~2M speeches / 2.7M embedded chunks currently
- *LegisInfo + each legislature's bills pages* — 18.8k bills across 10 legislatures so far
- *changedetection.io + custom DNS/GeoIP/TLS scanners* — for the hosting-sovereignty layer
- *Embeddings* — self-hosted Qwen3-Embedding-0.6B (1024-dim, fp16) on a laptop RTX 4050, ~51 chunks/sec end-to-end. Wanted to keep hosted-API costs out of the critical path. Chugs along nicely — curious if that matches what you've seen work for civic-tech scale
- *Socials backfill* — `extra.urls` as seed, then a web-search + LLM-parse agent (Anthropic API) for the residual gaps. Still iterating on how to do that without burning tokens

**Represent gaps I've hit** (the part that's probably most useful for a "where should we invest?" conversation)

- Empty `url` / `personal_url` on many BC, ON, NB, NL, Yukon reps — I wrote direct per-legislature scrapers to fill those in
- No representative-set for the Nunavut Legislative Assembly (all candidate slugs return zero objects; no `nunavut-electoral-districts` boundary set either)
- Uneven `boundary_url` on NB reps — postcode lookup still works, but `/representatives/` doesn't round-trip to a boundary
- Municipal `extra.urls` socials coverage varies a lot city-to-city
- A handful of non-obvious canonical slugs (`quebec-assemblee-nationale`, `pei-legislature`, `yukon-legislature`, `northwest-territories-legislature`) — some of the plan-suggested slugs return zero results

Happy to share the gap-scraper notes, constituency-ID mappings, or patches back — whatever's useful on your end.

And to be direct about (2) from my original email: I'm genuinely interested in where you're taking Represent and whether there's collaboration or career space. No pressure to turn the call into anything formal; just worth naming.

Here's my booking link: [LINK]. Grab whatever works over the next two weeks — I'm flexible.

Cheers,
Reed

---

## Pre-send checklist

1. Paste your booking link in for `[LINK]`.
2. Re-check the numbers in the appendix below against the live DB.
3. Decide whether to keep the "career space" sentence — it's direct, which is usually good, but drop it if the tone of M.'s reply reads more "friendly peer chat" than "hiring-adjacent."
4. Consider attaching or linking canadianpoliticaldata.ca again at the end if this is a different thread than the original intro email.

## Numbers to refresh before sending

Run these to confirm the counts still match what's in the draft:

```sql
-- "~1,786 politicians in my DB sourced from Represent"
SELECT COUNT(*) FROM politicians WHERE source_id LIKE 'opennorth:%';

-- Federal / provincial / municipal split
SELECT level, COUNT(*) FROM politicians
 WHERE source_id LIKE 'opennorth:%' GROUP BY 1 ORDER BY 1;

-- "1,454 polygons currently"
SELECT COUNT(*) FROM constituency_boundaries;

-- "~2M speeches / 2.7M embedded chunks"
SELECT COUNT(*) FROM speeches;
SELECT COUNT(*) FROM speech_chunks;

-- "18.8k bills across 10 legislatures"
SELECT COUNT(*) AS bills,
       COUNT(DISTINCT province_territory) AS jurisdictions
  FROM bills;
```

Qwen3 throughput (~51 chunks/sec end-to-end) is from the 2026-04-18 re-embed in `CLAUDE.md` — refresh if a newer benchmark exists.
