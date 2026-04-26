# Handoff: AI contradictions layer for grouped search

You're taking over to build the AI-assisted contradictions feature on top of the "By politician" grouped search view that already ships. Most of the scaffolding is done — grouped search is live, politicians are rendered in cards with their quotes across parliaments, quick-nav works, and the "Analysis" tab hosts the charts dashboard. What's still missing is the AI layer that reads a politician's matching quotes and flags candidate contradictions with a one-line rationale per pair.

This doc is everything you need to pick up. Read `docs/goals.md` and `docs/architecture.md` first if you haven't.

---

## Why this is v1.1 and not v1

The core insight: embeddings cluster by **topic**, not **stance**. Two opposite statements about the carbon tax land close together in Qwen3-Embedding-0.6B space because they share the same topic. A naïve "reverse embedding search" does not produce contradictions — it produces same-topic echoes. Real contradiction detection needs either a purpose-built NLI model (DeBERTa-MNLI) or a generative LLM that can read a pair of quotes and decide whether they contradict.

v1 shipped the candidate-generation half on purpose, framing the grouped view as "one politician's statements on this topic across parliaments" and leaving the *contradiction judgment* to the human reader. v1.1 adds the LLM layer: a button on each card that, **after an explicit consent modal**, sends that politician's matching chunks to a free-tier OpenRouter model and renders flagged pairs inline.

If you ever find yourself trying to "detect contradictions with embeddings alone," you've gone off-piste. Embeddings pick candidates; the LLM judges them; the human trusts neither without reading the source quotes.

---

## What's already shipped

You inherit all of this — do not rebuild it.

### Backend (`services/api/src/routes/search.ts`)

- `GET /api/v1/search/speeches` accepts `group_by=politician` and `per_group_limit` (1–10).
- Grouped mode: HNSW top-500 candidate pool → window-functioned top-M-per-politician → top-K-politicians. `SET LOCAL hnsw.ef_search = 600` inside a transaction.
- q-less grouped call returns 400. Only resolved politicians appear in groups (chunks with `politician_id IS NULL` drop out).
- Response shape when grouped:
  ```jsonc
  {
    "mode": "grouped",
    "group_by": "politician",
    "page": 1,
    "limit": 20,
    "per_group_limit": 5,
    "total_politicians": 20,
    "groups": [
      {
        "politician": { "id": "...", "name": "...", "slug": "...", "photo_url": "...", "party": "...", "socials": [...] },
        "best_similarity": 0.72,
        "chunks": [
          {
            "chunk_id": "...", "speech_id": "...", "chunk_index": 0,
            "text": "…", "snippet_html": "<b>…</b>", "similarity": 0.72,
            "spoken_at": "2024-03-21T00:00:00Z",
            "language": "en", "level": "federal", "province_territory": null,
            "party_at_time": "Conservative",
            "speech": {
              "speaker_name_raw": "...",
              "source_url": "...", "source_anchor": "...",
              "session": { "parliament_number": 44, "session_number": 1 }
            }
          }
        ]
      }
    ]
  }
  ```
- Timeline mode response is unchanged from before grouping landed. Fully backwards-compatible.
- Bug fix worth knowing: sessions are joined via `speeches.session_id`, not `speech_chunks.session_id` — the chunk-level copy diverges from the parent-speech copy on every federal row (1.4 M rows). If you touch session joins, keep going through `speeches`.

### Frontend

- `services/frontend/src/pages/HansardSearchPage.tsx` — the only search page. Route is `/search`, not `/hansard-search`.
- Three URL-backed tabs: `?view=timeline` (default) | `?view=politician` | `?view=analysis`.
- `services/frontend/src/components/PoliticianResultGroup.tsx` — one politician's header + chunks, with a horizontal parliament-divider line between consecutive chunks whose `parliament_number` differs. Anchors as `#pg-card-${politician.id}`.
- `services/frontend/src/components/PoliticianQuickNav.tsx` — horizontal strip of mini cards (face + name + count) above the groups; click smooth-scrolls to the card with a brief accent flash.
- `services/frontend/src/components/SearchDashboard.tsx` — the existing facets dashboard, now rendered only when `view=analysis` with `defaultOpen` so it arrives expanded.
- Discriminated-union response in `services/frontend/src/hooks/useSpeechSearch.ts`: `TimelineSearchResponse | GroupedSearchResponse`, keyed by `mode`. Any new consumer must narrow on `mode` before reading `items` or `groups`.

### Embedder — know what's actually running

`tei` container (`ghcr.io/huggingface/text-embeddings-inference:89-1.9`) serves `Qwen/Qwen3-Embedding-0.6B` fp16 on the RTX 4050 Mobile. BGE-M3 is **gone**; the `services/embed/` directory is legacy-on-disk only. Do not resurrect the old FlagEmbedding wrapper to add rerankers.

---

## What you're building

A user-triggered AI pass over the quotes shown inside one `PoliticianResultGroup` card, that returns candidate contradictions + one-line rationales, rendered inline below the politician's chunks (and optionally offered at "other identified locations" later — not in v1.1 scope).

The user's instructions on shape:

1. **"Analyze for contradictions (AI)"** button sits inside each result card — **not** as a peer tab.
2. The first click (per browser, per configured model) opens a **consent modal** that tells the user what's about to happen before any data leaves our servers.
3. Consent is stored in `localStorage`, keyed by the configured model name. If the model changes, the modal re-prompts. (Users consent to a specific model, not to "AI in general".)
4. The backend calls a **free-tier OpenRouter model** — not Anthropic — using an OpenAI-compatible request. OpenRouter routes the request to the underlying host.
5. Output is always framed as *the model suggests…*, never as a verdict.

### User flow

1. User searches on `/search`, switches to `By politician`.
2. Inside any politician's card, an "Analyze for contradictions (AI)" button sits in the card header or below the chunk list.
3. First click → consent modal:
   - Short feature description ("Sends this politician's quotes shown here to an AI model that looks for possible contradictions and returns short explanations.").
   - Exact configured model, verbatim, e.g. `google/gemini-2.0-flash-exp:free` via OpenRouter.
   - Third-party disclosure: quotes leave our servers for OpenRouter, which routes to the chosen model's host.
   - "Continue and analyze" / "Cancel" buttons. "Don't show this again for this browser" checkbox.
4. On Continue: POST to a new `/api/v1/contradictions/analyze` endpoint with `{ politician_id, query, chunk_ids }` — the chunks already shown in that card, so the model only sees what the user is looking at.
5. Endpoint bundles the chunks + metadata into a single structured-output OpenRouter call; returns flagged pairs + rationales.
6. Inline section below the card's chunks: "AI-flagged possible contradictions" with each pair highlighted and the model's one-line rationale. Loading state while in flight; clean error states on 401/429/timeout.
7. Subsequent clicks on the same browser with the same configured model skip the modal (consent already recorded). Clicks with a *different* configured model re-prompt.

---

## Architecture

### A. New API route: `services/api/src/routes/contradictions.ts`

Two endpoints, mounted under `/api/v1/contradictions`:

**`GET /meta`** — reports feature status and the configured model so the frontend can (a) grey the button out when unconfigured and (b) show the exact model name in the consent modal. Shape:
```jsonc
{ "enabled": true, "model": "google/gemini-2.0-flash-exp:free", "provider": "openrouter" }
```
If `OPENROUTER_API_KEY` is missing: `{ "enabled": false, "model": null, "provider": "openrouter" }`.

**`POST /analyze`** — body (zod-validated):
```jsonc
{
  "politician_id": "uuid",
  "query": "carbon tax",
  "chunk_ids": ["uuid", "uuid", ...]  // 2-10 ids, must all belong to politician_id
}
```

Steps:
1. Reject if feature disabled (503).
2. SELECT chunks from `speech_chunks` where `id = ANY($chunk_ids)` AND `politician_id = $politician_id`. If fewer rows come back than requested, 400 ("chunk does not belong to politician"). This closes the door on a caller asking the model about arbitrary speech rows.
3. Fetch the politician's name + current party from `politicians` for prompt context.
4. Join to `legislative_sessions` via `speeches.session_id` (not `speech_chunks.session_id` — see bug note above) to get parliament labels.
5. Build a structured-output prompt (see §D) and call OpenRouter.
6. Response shape:
   ```jsonc
   {
     "model": "google/gemini-2.0-flash-exp:free",
     "analyzed_chunk_ids": ["uuid", ...],
     "pairs": [
       {
         "a_chunk_id": "uuid",
         "b_chunk_id": "uuid",
         "kind": "contradiction" | "evolution" | "consistent",
         "rationale": "one-sentence explanation"
       }
     ],
     "summary": "optional one-sentence overall take"
   }
   ```
   Kinds:
   - `contradiction` → model believes the two statements reverse position.
   - `evolution` → same politician, softened/hardened stance without a clean reversal.
   - `consistent` → model explicitly asserts no contradiction between these two. (Return this too when the card produces no contradictions; an empty `pairs` array is ambiguous — an explicit "model found no contradictions" is better UX.)

**Error mapping:**
- OpenRouter 401 → 503 with body `{ error: "AI service auth failed" }` (don't leak the key state).
- OpenRouter 429 → 429 with body `{ error: "AI service rate-limited, try again in a moment" }`.
- Timeout / network → 504 with generic message.
- Model returns unparseable JSON → 502 with generic message; log the raw response server-side for debugging.

### B. Config

New env vars consumed by the API service (add to `services/api/src/config.ts`):

| Variable | Default | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | (unset) | Required to enable the feature. Empty → `enabled: false`. |
| `OPENROUTER_CONTRADICTIONS_MODEL` | `nvidia/nemotron-3-super-120b-a12b:free` | Full OpenRouter model id. Surfaced to the frontend. Legacy `OPENROUTER_MODEL` is still read as a fallback (boot-time deprecation warning). |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint. |
| `OPENROUTER_SITE_URL` | `https://canadianpoliticaldata.ca` | Sent as `HTTP-Referer` for OpenRouter attribution. |
| `OPENROUTER_APP_NAME` | `Canadian Political Data` | Sent as `X-Title`. |
| `OPENROUTER_TIMEOUT_MS` | `30000` | Client-side request timeout. |

Docker-compose: add these to `api` and `scanner-jobs` services in `docker-compose.yml`. Update `.env.example` with descriptive comments. **Do not commit a real key to git.**

### C. OpenRouter call

OpenRouter is OpenAI-compatible. See `https://openrouter.ai/docs/quickstart`. The minimal request:

```ts
await fetch(`${baseUrl}/chat/completions`, {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${apiKey}`,
    "HTTP-Referer": siteUrl,
    "X-Title": appName,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: userPayload },
    ],
    response_format: { type: "json_object" },
    temperature: 0.2,
  }),
});
```

**Do not use tool/function calling for the first pass** — free-tier models' tool support is uneven. `response_format: { type: "json_object" }` + an explicit JSON schema in the system prompt is more portable across free-tier models.

### D. Prompt structure

System prompt spells out:
- The task: given N quotes from a single Canadian politician on a specific topic, return a JSON object listing pairs that contradict, evolve, or are consistent.
- The JSON schema (copied into the prompt verbatim).
- Calibration: the model is *not* a political analyst. It should favour **contradiction** only when the two statements make directly opposite claims. Policy evolution and same-bill-different-clause nuance should go in `evolution` or `consistent`, not `contradiction`.
- Canadian context: parliament labels, party changes, and party-line vs personal-position distinctions exist. A politician's position changing after a party change is worth flagging as `evolution`, not a personal contradiction.

User message payload is a structured dump:

```
Politician: Ziad Aboultaif (Conservative)
Query topic: carbon tax

Quote A (chunk_id=...):
  Date: 2016-10-07
  Parliament: 42nd, Session 1
  Party at time: Conservative
  Text: "…"

Quote B (chunk_id=...):
  …
```

Keep quote text length capped (e.g. 800 chars per chunk) to bound context usage on free-tier rate limits. If a chunk's text exceeds that, truncate with an explicit `…[truncated]` marker.

### E. Frontend components

New files:

- `services/frontend/src/components/AIConsentModal.tsx` — modal with the disclosures above. Accepts `model` prop (renders it verbatim), `onContinue`, `onCancel`. Keyboard-trap focus, ESC to cancel.
- `services/frontend/src/components/AIContradictionAnalysis.tsx` — the per-card section. Owns:
  - The "Analyze for contradictions (AI)" button (greyed with a tooltip when `meta.enabled === false`).
  - Consent check against `localStorage` key `cpd_ai_analyze_consent_v1` (value: `{ model: string, consented_at: ISO8601 }`).
  - The modal mount/dismount.
  - The `fetch('/api/v1/contradictions/analyze', …)` call and its loading/error/success states.
  - The inline render of returned pairs: two-quote rows side-by-side on desktop, stacked on mobile, each badged with the `kind` and showing the rationale.

New hook:

- `services/frontend/src/hooks/useAIAnalyzeMeta.ts` — fetches `/api/v1/contradictions/meta` once per page load; returns `{ enabled, model, loading, error }`. Don't re-fetch on every card — one hook at the page level, pass `meta` down as a prop (or expose via context).

Modifications:

- `PoliticianResultGroup.tsx` — accept an optional child/slot prop for the analysis section, or render `<AIContradictionAnalysis />` conditionally based on a `showAnalyze` flag. Prefer the slot approach so the group component stays UI-pure and the page controls which cards get the button (in case you want it on selected cards only later).
- `HansardSearchPage.tsx` — when `view === 'politician'`, pass the meta + an `AIContradictionAnalysis` slot into each `PoliticianResultGroup`. Don't render the button on the Analysis or Timeline tabs.
- `services/frontend/src/styles/hansard-search.css` — styles for the button, the consent modal (backdrop + dialog), the inline results section with `kind` badges.

### F. Consent persistence rules

localStorage key: `cpd_ai_analyze_consent_v1`, value shape:
```jsonc
{ "model": "google/gemini-2.0-flash-exp:free", "consented_at": "2026-04-19T18:23:00Z" }
```

Re-prompt rules:
- Missing entry → prompt.
- Entry present but `model !== meta.model` → prompt and overwrite on Continue.
- Entry present with matching model → skip the modal.

Add a small "Review AI settings" link in the card or page footer that clears the key and re-prompts next click. Don't wire this into user profiles or server state — localStorage only.

---

## Build sequence

1. **Meta endpoint** (`GET /api/v1/contradictions/meta`) + config wiring. Hit it with curl in both configured and unconfigured states. This lets the frontend know whether to render the button at all.
2. **Analyze endpoint stub** — route wired, zod validation on body, chunk-ownership check against DB, returns a hand-coded fake response. Confirm the frontend can display flagged pairs before you involve OpenRouter.
3. **Real OpenRouter call** — replace the stub with the actual request. Test on a known cross-parliament politician (e.g. Ziad Aboultaif on carbon tax — the card has 4 quotes across the 42nd and 44th parliaments, which is a clean sanity test). Eyeball whether the model's `contradiction`/`evolution`/`consistent` labels match your intuition.
4. **Consent modal + persistence** — modal renders, stores in localStorage, re-prompts on model change, keyboard/ESC behaviour correct.
5. **Error + loading states** — simulate: no key configured, 429 from OpenRouter (set an obviously-invalid model to force 400/404 from OpenRouter, or mock), timeout, unparseable model output. Each gets a graceful UI message.
6. **Mobile** — 375 px width: modal fits, per-pair rows stack, rationale text doesn't overflow.
7. **Eyeball 20–30 real cards** on varied queries. Decide:
   - Is the rate of useful contradictions high enough to surface the button by default?
   - Or should the button only appear on cards with ≥2 parliaments (filter client-side on `parliament_count > 1`)?
   - Are there specific false-positive patterns worth prompt-tuning for (quoted opponents, read-into-record)?

Stages 1–4 are testable in isolation. Stage 7 is a *product* checkpoint, not just a code checkpoint.

---

## Verification

- **Meta round-trip.** `curl /api/v1/contradictions/meta` returns `{ enabled: true, model: "<configured>" }` with key set; `{ enabled: false, model: null }` when unset. Frontend button greys accordingly with a tooltip.
- **Analyze happy path.** On Ziad Aboultaif's carbon-tax card, the call returns 2–4 pairs, at least one of which is labelled `contradiction` or `evolution` with a plausible rationale. The rationale text reads as something a reasonable person would agree with.
- **Analyze chunk-ownership guard.** Send `chunk_ids` from a different politician and confirm 400, not 500 and not a silent mis-analysis.
- **Consent modal.** First click opens modal with the configured model name rendered verbatim. Continue → analyze fires; Cancel → nothing fires and no localStorage write. "Don't show again" persists consent; reloading the page and clicking again skips the modal. Changing `OPENROUTER_CONTRADICTIONS_MODEL` env var (rebuild API), reloading, and clicking re-prompts.
- **429 path.** Force a rate-limit (small free-tier quota — hit it ~10 times fast, or mock the response). UI shows "AI service rate-limited", not a 500.
- **Unconfigured path.** `docker compose up -d api` with `OPENROUTER_API_KEY` empty → button greys with tooltip; if forced, endpoint returns 503.
- **Mobile.** 375 px: modal is readable, buttons tap-friendly, result pairs stack, no horizontal scroll.
- **No regressions.** Grouped view, timeline view, and Analysis tab still behave as before. Existing `useSpeechSearch` callers unaffected.

---

## Deliberate non-goals for v1.1

- No NLI classifier (DeBERTa-MNLI, etc.) — OpenRouter LLM only.
- No pre-computation / caching of analysis results. Every click is a fresh call. Caching is a v1.2 decision once we see hit patterns.
- No background batch runs — the feature is user-triggered, full stop.
- No auto-surface on politician profiles. The button only exists in the grouped search view's result cards.
- No cross-politician analysis — one politician per call.
- No votes-table integration (`0018_votes.sql` is still unapplied; keep it out of scope).
- No multi-model comparison (letting users pick "analyze with model X vs Y"). One configured model at a time.

---

## Open risks and gotchas

- **Free-tier rate limits.** OpenRouter's free-tier endpoints have per-model rate limits that change without notice. Expect 429s in production once the feature gets attention. The error message must clearly say "rate-limited, try again later" — don't just show a generic failure.
- **Free-tier model drift.** Which models are free on OpenRouter changes over time. The env-var + consent-re-prompt design was deliberately built for this — when a model goes paid or disappears, operators swap `OPENROUTER_CONTRADICTIONS_MODEL` and every user re-consents on next click.
- **Quoted opponent / read-into-record.** A politician quoting an opponent's position sits in a chunk labelled with the politician's own `party_at_time`. The model has no inherent way to know the chunk's rhetorical frame. Mitigation ideas (pick one when you see the false-positive rate in practice):
  - Filter out chunks whose text begins with `"The member opposite said"` or ends with the closing quote of a long block quote. Brittle.
  - Add a note in the system prompt instructing the model to flag quote-markers and downgrade those pairs.
  - Surface the full chunk context (previous + next chunk) to the model so it can judge rhetorical framing itself. Increases token cost on free tier.
- **Party change = automatic "evolution."** A politician who switched parties will almost always produce `evolution` labels across the split, which is correct but can flood the UI. Consider deduplicating pairs that share a party-transition boundary.
- **Defamation exposure.** The output is public (rendered in a user's browser, user can share screenshots). The framing copy must read as "the model suggests…" throughout. Never render "contradiction" as a verdict phrase.
- **Bug fix dependency.** The `speeches.session_id` vs `speech_chunks.session_id` divergence (all 1.4 M federal rows) was fixed in the timeline/grouped SQL but the broader codebase may still have joins on the wrong column. If your parliament labels come back null on federal, that's why — join via `speeches`.

---

## Files you'll create or touch

**Create:**
- `services/api/src/routes/contradictions.ts`
- `services/frontend/src/components/AIConsentModal.tsx`
- `services/frontend/src/components/AIContradictionAnalysis.tsx`
- `services/frontend/src/hooks/useAIAnalyzeMeta.ts`

**Modify:**
- `services/api/src/index.ts` — register contradictions routes (`await app.register(contradictionsRoutes, { prefix: "/api/v1/contradictions" })`).
- `services/api/src/config.ts` — add the `OPENROUTER_*` env vars.
- `docker-compose.yml` — pass env vars to `api` (and `scanner-jobs` if you foresee reusing the client from scanner jobs).
- `.env.example` — document the vars with comments.
- `services/frontend/src/pages/HansardSearchPage.tsx` — render the analysis slot on grouped-view cards.
- `services/frontend/src/components/PoliticianResultGroup.tsx` — accept the slot (or a `showAnalyze` flag).
- `services/frontend/src/styles/hansard-search.css` — styles for the button, modal, inline result section.

**No DB migration required.**

---

## When in doubt

- **If the model's output is hot garbage on the first few cards**, the fix is almost always prompt calibration, not model choice. Tighten the definition of "contradiction" in the system prompt before swapping the configured model.
- **If consent feels heavy**, don't water it down. The transparency is the point — this is a public-interest tool; users should know their quotes are leaving our servers.
- **If OpenRouter is flaky and you're tempted to add a hosted-Anthropic fallback**, check with the user first. The explicit choice was OpenRouter free-tier to keep the feature free-to-use and vendor-neutral; falling back to a paid vendor changes that tradeoff.
- **If you're about to introduce a DB cache of results**, keep it keyed by `(chunk_ids hashed, model)` so a model change invalidates entries automatically.
