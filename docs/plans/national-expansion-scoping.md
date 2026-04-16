# National Political Data Expansion — Scoping Questions

**Status:** Active — awaiting user answers.
**Owner:** @adminatthebunker
**Last updated:** 2026-04-16

## Purpose

SovereignWatch is expanding from "where are Canadian political websites hosted?" into the **leading source of Canadian political data**, with an eventual goal of semantic search over what politicians are saying on any topic, at every level of government.

This doc pins down scope, goals, and architectural choices before we write any more code. Each question is followed by an empty **Answer:** block. Fill in what you can; leave "TBD" where you're not ready. Feed it back and we'll turn your answers into:

1. `docs/goals.md` — one-page north star
2. `docs/plans/semantic-layer.md` — schema + vector store + embedding plan
3. Updates to `CLAUDE.md` so every future agent inherits the conventions

---

## Project snapshot (as of 2026-04-16)

Read first — answers below should be consistent with or deliberately break from this baseline.

### Data on hand

| Layer | Count | Notes |
|---|---:|---|
| Politicians | 1,815 | 343 federal + 808 provincial + 571 municipal, 13 jurisdictions |
| Websites tracked | 2,348 | All scanned for sovereignty tier |
| Bills | 3,876 | NS (3,522) / AB (114) / ON (102) / QC (102) / BC (36) |
| Bill events | 5,078 | First reading, royal assent, etc. |
| Bill sponsors | 361 (360 FK-linked = 99.7%) | Linked via jurisdiction-specific IDs |
| Social handles | 4,112 | URLs only; no post content |
| Committee memberships | 408 | |
| openparliament cache | 4 rows (3 with activity) | Federal-only, lazy-fetched |
| Hansard / speeches | **0** | Not yet modeled |
| Votes / divisions | **0** | Not yet modeled |

### Stack

- Postgres 16 + PostGIS 3.4 (no pgvector yet)
- Fastify API, React+Vite+Leaflet frontend
- Python async scanner (Click CLI), cron sidecar
- Docker Compose on a single host, Pangolin tunnel to public

### Critical gaps for the stated goal

1. No speeches/Hansard table — the semantic-search payload doesn't exist.
2. No votes/divisions — can't do "what did they *do* vs. *say*".
3. No `pgvector` extension and no embedding column anywhere.
4. No press releases, no social post content (handles only).
5. 8 jurisdictions (MB/SK/NB/NL/PE/YT/NT/NU) have zero legislative-activity coverage.

### Conventions already established (keep or break deliberately)

- **Jurisdiction-specific ID columns on `politicians`** (`ab_assembly_mid`, `lims_member_id`, `qc_assnat_id`, `ola_slug`, `nslegislature_slug`, `openparliament_slug`). Exact FK joins beat name-fuzz every time.
- **Discriminated tables** — one `bills` table, discriminated by `level` + `province_territory`. Same pattern should apply to speeches/votes.
- **`raw_html` alongside parsed fields** — re-parsing is cheaper than re-fetching, especially with WAFs.
- **5-tier probe hierarchy before writing a scraper** — RSS → Drupal JSON → iframe/subdomain → GraphQL in JS bundles → HTML.
- **Research-handoff rule** — user shares endpoint research before agent probes (per memory).

---

## 1. Product definition

### 1.1 User priority

Who is this for, in priority order?

**Candidate audiences:**
- Journalists ("what did X say about carbon pricing last year?")
- Academic / policy researchers (longitudinal corpora, bulk export)
- Advocacy organizations / NGOs (scorecards, alerts)
- Campaigns / parties (opposition research)
- Engaged citizens (the current postal-code lookup)
- Civic-tech developers (open API consumers)

**Answer:**
> This is going to be a staged system. Primarily we want to target (1) engaged citizens and then to maintian the project we need some kind of backend system for (2) lobbyists, journalists, and academics, and advocacy organizations. Thinking a public search system for the public, then backend tools (chat, semantic matching, etc) to pull some kind of revenue ventually. 

### 1.2 Shipped artifact

What does "v1 of semantic search" look like on the site? Pick any / all, rank them:
- Single search box → results list
- Topic dashboard ("climate mentions across all legislatures over time")
- "Compare politician A vs. B on topic X"
- Public API for third parties
- Scheduled alerts / RSS on topic matches
- Bulk data export (CSV / Parquet)

**Answer:**
> All of those honestly sound great - first goal is the single search box.  

### 1.3 Positioning

Is sovereignty / hosting tier still the lede of the whole site, or does the brand re-pitch around "definitive political data of Canada" with hosting as one lens?

**Answer:**
> I think definitive political data of Canada is where this is going. We still want to talk about the sovereignty and hosting for now / in the future however will not be the endpoint of project. 

### 1.4 Commercial model

- Free civic tool forever?
- API tiers for media orgs?
- Donation / grant funded?
- Mixed (free UI, paid API)?

This determines whether we can afford hosted embeddings at scale.

**Answer:**
> We want to have a public search and the api teirs for those who can pay. We would likely have to look into grant funding to maintain long term.

### 1.5 Timeline

- Alberta independence referendum: 2026-10-19. Is that the v1 forcing function?
- Is there a separate target for the semantic-search product?

**Answer:**
> Hmm a little bit of force there - initial focus however not necersarily the be all end all. No I dont think so - that sematic search I am thinking might be the lure? 

### 1.6 Non-goals

What is this project explicitly *not* trying to be?

**Answer:**
> We dont want to be just another a-political system - we want this to be a project defined by access to information as a right, and we also want to take strong progressive and democractic stances. 

---

## 2. Data scope — "what politicians are saying"

### 2.1 Which sources count as "saying"?

Tick each; mark P1/P2/P3 priority.

- [P1] Hansard floor speeches
- [P2] Committee transcripts
- [P2] Bill sponsorship + bill text
- [P1] Recorded votes / divisions
- [P3] Press releases from official sites
- [P2] Social media post content (not just handles)
- [P3] Campaign materials / platforms
- [P3] Media quotes (third-party, copyright-hard)

**Answer:**
> See above desingations in priority markings. We want to get what politicians have been saying and what they have been moving primarily. 

### 2.2 Historical depth per source

- Current session only?
- Current parliament?
- Back to Hansard digitization (~1970s for most; 1994 for NS; 1996 for SK/PEI)?
- Openparliament.ca's full archive (1994+)?

**Answer:**
> We want to get as much of the digitized records as possible. More data is fine. We want to focus on making sure we are always up to date though. 

### 2.3 Multilingual handling

- QC: FR primary + EN translation
- NU: EN + Inuktitut + Inuinnaqtun + FR
- NB: EN + FR (bilingual by law)

Options:
- (a) Embed in source language only
- (b) Embed both, let multilingual models (BGE-M3 / e5) bridge at retrieval
- (c) Normalize to EN via MT, embed once

**Answer:**
> Hmm lets do B however if easier / cheaper its okay to just do C

### 2.4 Coverage levels

- Federal MPs + senators?
- All 10 provinces + 3 territories?
- Municipal council debates (much harder; often video-only)?

**Answer:**
> We want to start with all federal and all provincial - eventually work on larger municipal councils 

### 2.5 Non-elected figures

Do we extend beyond politicians to:
- Political staffers
- Former politicians (still relevant for historical corpora)
- Candidates (not yet elected)
- Party leaders never elected

**Answer:**
> For now, just elected figures. We can work on mapping other figures later. 

---

## 3. Semantic / retrieval architecture

### 3.1 Vector store

| Option | Pros | Cons |
|---|---|---|
| pgvector in existing Postgres | Single DB, trivial joins to `politicians`, easy ops, SQL-native filters | Max practical scale ~10M chunks with HNSW; needs tuning |
| Dedicated (Qdrant / Weaviate / LanceDB) | Better scale, better index types | Cross-DB joins are painful, two ops surfaces |
| Hosted (Pinecone / Turbopuffer) | Zero ops | Costs money monthly, vendor lock-in |

Full-scope estimate: federal Hansard ~250k speeches × 10 provinces × 20 years ≈ **5–10M chunks**.

**Answer:**
> hmm we want to keep things as local as possbile and we are bootstrapped to heck / no budget. It is okay if something takes a long time to process / we can move the system to one of my beefier machines later 

### 3.2 Embedding model

| Option | Cost at 10M × 500 tok | Notes |
|---|---|---|
| OpenAI text-embedding-3-large | ~$650 one-shot + incremental | 3072-dim; well-known |
| Voyage voyage-3 | ~$900 one-shot | Often wins political/legal benchmarks |
| Cohere embed-v3 | ~$1000 one-shot | Multilingual strong |
| Self-hosted BGE-M3 | GPU-less CPU inference possible; ~1 hour/100k chunks | Multilingual, no vendor |
| Self-hosted GTE-large | Similar | English-strong |

**Answer:**
> We want to do self-hosted for sure. If needed, can do this process on one of my beefier machines although the local enviroment is pretty strong. 

### 3.3 Chunking strategy

Speeches have natural boundaries (one speaker turn = one chunk). Do we:
- (a) One chunk per speaker turn (simplest, politician_id attaches cleanly)
- (b) Paragraph-level within speeches
- (c) Fixed token-count (e.g., 512 tok) with overlap

**Answer:**
> hmm one chunk would be slick however knowing that all places do this differently we may be focred to do it by token. 

### 3.4 Hybrid search

- BM25 (Postgres `tsvector`) + dense retrieval + reranker?
- Dense-only?
- Hybrid especially matters for proper nouns (bill numbers, act names, ridings).

**Answer:**
> hmm this ill leave up to you - it kinda depends what we can fetch however more is usually betgter 

### 3.5 Reranker

- Cohere Rerank (~$2/1M tokens)?
- BGE-reranker self-hosted?
- None for v1?

**Answer:**
> self hosted if possible 

### 3.6 Freshness SLA

How soon after a speech happens should it be retrievable?
- Hours (daily re-index cron)?
- Minutes (streaming ingest)?
- Next-day is fine?

**Answer:**
> Next day is fine / overnight would be good 

---

## 4. Entity / identity model

### 4.1 Person-over-time

If someone served as ON MPP then became federal MP, do we want:
- (a) Three rows linked by a canonical-person id
- (b) One row with a `politician_terms` history (table exists, sparsely populated)
- (c) Full OpenCivicData `ocd-person/*` adoption

**Answer:**
> hmm this we need for the canadian context and not sure it exists (we likely dont need to conform to the opencivicdata standard). We do want to have political terms as a readout. I think I would leave this up to your judgement. 

### 4.2 Constituency over time

Ridings redraw every census. Do we need "who represented Nepean in 2008"? If yes, we need historical riding snapshots in `constituency_boundaries`.

**Answer:**
> yes we want historical snapshots for sure 

### 4.3 Party over time

`politicians.party` is current-only. For "what did Liberals say in 2015 vs. now" we need party-at-time-of-speech.

**Answer:**
> yes part at time of speech would be great

### 4.4 Roles

Cabinet posts, critic portfolios, committee chairs — do we model role history, or only current role?

**Answer:**
> if we can, role history, however focus is just the current role / ongoing. 

---

## 5. Scraping / coverage strategy

### 5.1 Research-handoff rule

Current memory says: pause and ask user for research before probing any of MB/SK/NB/NL/PE/YT/NT/NU. Keep as-is, or flip to "probe autonomously unless past findings exist"?

**Answer:**
> nah I want to do initial finding of webpages - trying to save context here and sometims I notice things the machine does not 

### 5.2 PDF pipeline

AB Hansard is PDF-only; SK/MB/NL/PE archives are PDF-heavy. Do we invest in a reusable PDF→structured-text pipeline (pdfplumber + speaker-turn regex + LLM fallback) now, or defer?

**Answer:**
> yeah we are going to need a pdf extractor for sure - might as well think about how to do this 

### 5.3 Blocked jurisdictions

- **Yukon** (Cloudflare Bot Management)
- **PEI** (Radware ShieldSquare)

Options:
- (a) Invest in Playwright/Camoufox infra
- (b) Email legislatures for civic-transparency allowlist (worked elsewhere)
- (c) Skip until vendor changes

**Answer:**
> hmm will have to get the infra going eventually however would rather flag systems that are hard to access and worry about later. Maybe we make a data-table so we can track this in app too 

### 5.4 Outreach

Should we proactively email every province's legislative library introducing SovereignWatch and asking for API access or crawl allowlisting? Cheap and often works.

**Answer:**
> This is a maybe for sure. I would like to have something seperate from government oversight. 

### 5.5 Agent coordination

You mentioned another agent is automating scanning. What exactly is that agent's scope — full ingestion pipelines (bills+speeches+votes), or just URL/endpoint discovery? This affects what we lock in now vs. leave pluggable.

**Answer:**
> I meant chat - just working with another code cli claude to do the passes on legislatures

---

## 6. Legal / governance

### 6.1 QC commercial restriction

QC Crown copyright requires prior permission for commercial use. If we stay non-commercial we're clear; if we ever monetize we need to write to the National Assembly. Current posture?

**Answer:**
> We are not commercial - no worries here

### 6.2 Redistribution policy

- Full speech text with citation?
- Snippet + link-out only?
- Full for non-commercial, snippet for API consumers?

**Answer:**
> full for non-cmeercial, snippet for api cconsumers 

### 6.3 PII in Hansard

Petitioners, victims, witnesses are named in the record. Do we:
- Index and surface as-is?
- Redact non-politician names at ingest?
- Flag but not hide?

**Answer:**
> yeah we dont need non-politicians however does not need redaction - just pass over and surface when needed 

### 6.4 Takedown / correction policy

Do we have one? Who owns it?

**Answer:**
> We do not have one yet - should build this in 

---

## 7. Quality / trust

### 7.1 Provenance UI

Every surfaced claim → click → exact source paragraph + URL + date?
Requires schema: `source_url`, `source_paragraph_id`, `source_char_offset` on every chunk.

**Answer:**
> hmm this would be useful however if is too cumbersome not too worried about it 

### 7.2 Misattribution handling

Hansard has OCR / transcription errors. Do we:
- Mark `speaker_id_confidence`?
- Allow user corrections?
- Both?

**Answer:**
> Both for sure. Thinkig will setup a email smtp system for people to send in corrections? 

### 7.3 Dedup

Bills carry over across sessions with new numbers. Speeches get quoted back. Committee remarks repeat in reports. Do we dedup at ingest, at index, or at query time?

**Answer:**
> Dedup at injest - we want to avoid the bloat for sure 

### 7.4 Accuracy baseline

What error rate is acceptable for v1 retrieval? 1% wrong attribution? 5%?

**Answer:**
> Hmm we can be generous in v1 - lets say up to 5% 

---

## 8. For the "other agent" doing scans in parallel

Drop-in recommendations we should agree on before they bake decisions in:

1. **Always capture a stable integer/slug ID per politician from the source system** (`ab_assembly_mid`, `lims_member_id`, `qc_assnat_id`-style). Add `politicians.<jurisdiction>_id` per province. Skip this → fight name-fuzz forever.
2. **Store `raw_html` / `raw_text` alongside parsed fields.** Re-parsing beats re-fetching; often the only option under WAFs.
3. **Model `speeches` now, even if only one jurisdiction ingests first.** Suggested shape:
   ```
   speeches (id, session_id, politician_id, speaker_name_raw,
             speech_type, spoken_at, sequence, text, source_url, raw)
   speech_references (speech_id, bill_id | committee_id)
   ```
   Discriminated by `level` + `province_territory` — same pattern as `bills`.
4. **Follow the 5-tier probe hierarchy** (RSS → Drupal JSON → iframe/subdomain → GraphQL in JS bundles → HTML).
5. **Do not unify `votes` before seeing two jurisdictions' real data.** NT/NU consensus gov't breaks any simple `vote_party` / `roll_call` model.
6. **Build PDF extractor once, not per-province.** Speaker-turn regex + section headers is 80%; per-province templates tune the rest.
7. **Rate-limit and cache persistently** — log every request by URL+etag; re-runs should be free.
8. **Archive raw scraped artifacts (HTML/PDF hash) to content-addressable storage** — even if we don't need it today, re-parsing archival text is how civic-tech projects stay accurate over a decade.

**Anything to add, remove, or change?**

> Looks bgood. 

---

## 9. Open questions I haven't thought to ask

**What else should be scoped that isn't above?**

> N/A

---

## Next steps after this doc is filled

1. Extract answers into `docs/goals.md` (north star, non-goals, audience)
2. Extract architecture answers into `docs/plans/semantic-layer.md` (schema, vector store, embedding, chunking, provenance)
3. Write / update root `CLAUDE.md` with conventions so every future agent inherits them
4. Open tickets / tasks per jurisdiction for the remaining 8 legislative pipelines, each gated on your research-handoff rule
