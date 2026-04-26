import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import {
  effectivePoliticianIds,
  MAX_POLITICIAN_PINS,
  SPEECH_TYPE_VALUES,
  useSpeechSearch,
  useSpeechSearchMeta,
  type PoliticianSort,
  type SpeechSearchFilter,
  type SpeechType,
} from "../hooks/useSpeechSearch";
import { SpeechFilters } from "../components/SpeechFilters";
import { SpeechResultCard } from "../components/SpeechResultCard";
import { SearchDashboard } from "../components/SearchDashboard";
import { SaveSearchButton } from "../components/SaveSearchButton";
import { PoliticianResultGroup } from "../components/PoliticianResultGroup";
import { PoliticianQuickNav } from "../components/PoliticianQuickNav";
import { PoliticianPinChips } from "../components/PoliticianPinChips";
import { AIContradictionAnalysis } from "../components/AIContradictionAnalysis";
import { AIFullReportButton } from "../components/AIFullReportButton";
import { useAIAnalyzeMeta } from "../hooks/useAIAnalyzeMeta";
import { useReportsMeta } from "../hooks/useReportsMeta";
import { useUserAuth } from "../hooks/useUserAuth";
import "../styles/hansard-search.css";

type ViewMode = "timeline" | "politician" | "analysis";

function readView(params: URLSearchParams): ViewMode {
  const v = params.get("view");
  if (v === "politician") return "politician";
  if (v === "analysis") return "analysis";
  return "timeline";
}

const POLITICIAN_SORTS: readonly PoliticianSort[] = [
  "mentions",
  "best_match",
  "avg_match",
  "keyword_hits",
] as const;

function readSort(params: URLSearchParams): PoliticianSort {
  const s = params.get("sort");
  return (POLITICIAN_SORTS as readonly string[]).includes(s ?? "")
    ? (s as PoliticianSort)
    : "mentions";
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function parseMinSimilarity(raw: string | null): number | undefined {
  if (!raw) return undefined;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0 || n > 1) return undefined;
  return n;
}

function parsePositiveInt(raw: string | null): number | undefined {
  if (!raw) return undefined;
  const n = Number(raw);
  if (!Number.isInteger(n) || n <= 0) return undefined;
  return n;
}

function parseSpeechTypes(params: URLSearchParams): SpeechType[] | undefined {
  const allowed = new Set<string>(SPEECH_TYPE_VALUES);
  const seen = new Set<SpeechType>();
  const out: SpeechType[] = [];
  for (const v of params.getAll("speech_type")) {
    if (allowed.has(v) && !seen.has(v as SpeechType)) {
      seen.add(v as SpeechType);
      out.push(v as SpeechType);
    }
  }
  return out.length > 0 ? out : undefined;
}

function readFilter(params: URLSearchParams): SpeechSearchFilter {
  const lang = params.get("lang");
  const level = params.get("level");
  const view = readView(params);
  const rawIds = params.getAll("politician_id").filter(v => UUID_RE.test(v));
  // Dedupe while preserving order; cap at 10 to match API.
  const seen = new Set<string>();
  const politician_ids: string[] = [];
  for (const id of rawIds) {
    if (!seen.has(id) && politician_ids.length < 10) {
      seen.add(id);
      politician_ids.push(id);
    }
  }
  // Parliament + session must arrive together; one without the other is
  // ambiguous (which session of which parliament?) so drop both.
  const parliament = parsePositiveInt(params.get("parliament"));
  const session = parsePositiveInt(params.get("session"));
  const havePair = parliament != null && session != null;
  return {
    q: params.get("q") ?? "",
    lang: (lang === "en" || lang === "fr" || lang === "any" ? lang : "any") as SpeechSearchFilter["lang"],
    level: (level === "federal" || level === "provincial" || level === "municipal"
      ? level
      : undefined) as SpeechSearchFilter["level"],
    province_territory: params.get("province") ?? undefined,
    politician_ids: politician_ids.length > 0 ? politician_ids : undefined,
    party: params.get("party") ?? undefined,
    from: params.get("from") ?? undefined,
    to: params.get("to") ?? undefined,
    exclude_presiding: params.get("exclude_presiding") === "true" ? true : undefined,
    min_similarity: parseMinSimilarity(params.get("min_similarity")),
    parliament_number: havePair ? parliament : undefined,
    session_number: havePair ? session : undefined,
    speech_types: parseSpeechTypes(params),
    page: Number(params.get("page")) || 1,
    limit: 20,
    group_by: view === "politician" ? "politician" : "timeline",
    per_group_limit: view === "politician" ? 5 : undefined,
    sort: view === "politician" ? readSort(params) : undefined,
  };
}

function writeFilter(f: SpeechSearchFilter, view: ViewMode): URLSearchParams {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.lang && f.lang !== "any") p.set("lang", f.lang);
  if (f.level) p.set("level", f.level);
  if (f.province_territory) p.set("province", f.province_territory);
  if (f.party) p.set("party", f.party);
  if (f.from) p.set("from", f.from);
  if (f.to) p.set("to", f.to);
  if (f.exclude_presiding) p.set("exclude_presiding", "true");
  if (f.min_similarity != null && f.min_similarity > 0) {
    p.set("min_similarity", String(f.min_similarity));
  }
  if (f.parliament_number != null && f.session_number != null) {
    p.set("parliament", String(f.parliament_number));
    p.set("session", String(f.session_number));
  }
  if (f.speech_types && f.speech_types.length > 0) {
    for (const t of f.speech_types) p.append("speech_type", t);
  }
  if (f.politician_ids && f.politician_ids.length > 0) {
    for (const id of f.politician_ids) p.append("politician_id", id);
  } else if (f.politician_id) {
    p.set("politician_id", f.politician_id);
  }
  if (f.page && f.page > 1) p.set("page", String(f.page));
  if (view === "politician") p.set("view", "politician");
  if (view === "analysis") p.set("view", "analysis");
  if (view === "politician" && f.sort && f.sort !== "mentions") p.set("sort", f.sort);
  return p;
}

const SORT_LABELS: Record<PoliticianSort, string> = {
  mentions: "Most mentions",
  best_match: "Strongest match",
  avg_match: "Avg quality",
  keyword_hits: "Keyword hits",
};

const SORT_DESCRIPTORS: Record<PoliticianSort, string> = {
  mentions: "ranked by number of on-topic quotes",
  best_match: "ranked by strongest single match",
  avg_match: "ranked by average match quality",
  keyword_hits: "ranked by exact keyword hits",
};

export default function HansardSearchPage() {
  useDocumentTitle("Hansard Search");
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const view = useMemo(() => readView(searchParams), [searchParams]);
  const filter = useMemo(() => readFilter(searchParams), [searchParams]);

  // Local, immediate text value so typing feels instant; the URL +
  // upstream query only update after a debounce.
  const [qDraft, setQDraft] = useState(filter.q ?? "");
  const debounceTimer = useRef<number | null>(null);

  // Keep the draft synced when the URL changes externally (back button etc.)
  useEffect(() => {
    setQDraft(filter.q ?? "");
  }, [filter.q]);

  const applyPatch = (patch: Partial<SpeechSearchFilter>) => {
    const next = { ...filter, ...patch };
    setSearchParams(writeFilter(next, view), { replace: false });
  };

  const setView = (next: ViewMode) => {
    if (next === view) return;
    // Reset to page 1 on view change so users don't land on a p>1 that
    // happens to be empty in the other view.
    const nextFilter = { ...filter, page: 1 };
    const params = writeFilter(nextFilter, next);
    setSearchParams(params, { replace: false });
  };

  const onQChange = (next: string) => {
    setQDraft(next);
    if (debounceTimer.current) window.clearTimeout(debounceTimer.current);
    debounceTimer.current = window.setTimeout(() => {
      applyPatch({ q: next, page: 1 });
    }, 300);
  };

  const onQSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (debounceTimer.current) window.clearTimeout(debounceTimer.current);
    applyPatch({ q: qDraft, page: 1 });
  };

  const pinnedIds = effectivePoliticianIds(filter);
  const pinnedSet = useMemo(() => new Set(pinnedIds), [pinnedIds.join(",")]);
  const pinCapReached = pinnedIds.length >= MAX_POLITICIAN_PINS;

  const togglePin = (id: string) => {
    const next = pinnedSet.has(id)
      ? pinnedIds.filter(p => p !== id)
      : pinnedIds.length < MAX_POLITICIAN_PINS
        ? [...pinnedIds, id]
        : pinnedIds;
    applyPatch({
      politician_ids: next.length > 0 ? next : undefined,
      politician_id: undefined,
      page: 1,
    });
  };

  const clearPins = () => {
    applyPatch({ politician_ids: undefined, politician_id: undefined, page: 1 });
  };

  const hasAnyFilter = Boolean(
    filter.level ||
      filter.province_territory ||
      filter.party ||
      filter.from ||
      filter.to ||
      pinnedIds.length > 0 ||
      (filter.parliament_number != null && filter.session_number != null) ||
      (filter.speech_types && filter.speech_types.length > 0),
  );
  const hasQuery = Boolean(filter.q && filter.q.trim());
  // Grouped mode is semantic-only (the API 400s on a q-less grouped call).
  // Timeline still allows filter-only searches.
  const enabled = view === "politician" ? hasQuery : (hasQuery || hasAnyFilter);
  // Pins filter all three views so the results visibly narrow. To keep
  // "I can add more pins" possible even when the grid has collapsed to
  // just the pinned cards, the chip row hosts a typeahead picker
  // (PoliticianPinChips → PoliticianPinPicker).
  const { data, loading, error } = useSpeechSearch(filter, enabled);
  const meta = useSpeechSearchMeta();
  // Single meta fetch for the whole page (cached module-level), so
  // rendering 20 grouped cards doesn't produce 20 /contradictions/meta
  // calls. Only gets used on the politician view.
  const { meta: aiMeta } = useAIAnalyzeMeta();
  const { meta: reportsMeta } = useReportsMeta();
  // Auth state drives the anon "sign in to expand" banner above the
  // politician-grouped results, plus the per-card expand affordance
  // inside PoliticianResultGroup itself. `disabled` is true when the
  // server has accounts off (JWT_SECRET unset) — match SaveSearchButton's
  // posture and render no auth UI in that case.
  const { user, disabled: authDisabled } = useUserAuth();

  const page = filter.page ?? 1;
  const timeline = data && data.mode !== "grouped" ? data : null;
  const grouped = data && data.mode === "grouped" ? data : null;
  const pages = timeline?.pages ?? 1;
  const total = timeline?.total ?? 0;
  const dashboardTotal =
    timeline?.total ?? (grouped ? grouped.total_politicians : undefined);

  return (
    <section className="hansard-search">
      <header className="hansard-search__header">
        <h2 className="hansard-search__title">
          <abbr title="The official transcript of what was said in Parliament">Hansard</abbr>{" "}
          Search
        </h2>
        <p className="hansard-search__subtitle">
          Search Canadian parliamentary speeches by meaning, not just exact words. Try{" "}
          <em>"rising cost of groceries"</em> — you'll find speeches that say "food prices" too.
        </p>
        {meta.data && meta.data.coverage < 0.99 && (
          <p className="hansard-search__banner" role="status">
            Backfill in progress: {(meta.data.coverage * 100).toFixed(0)}% of{" "}
            {meta.data.total_chunks.toLocaleString()} chunks searchable
            ({meta.data.embedded_chunks.toLocaleString()} indexed). Historical Parliaments are
            being embedded now.
          </p>
        )}
      </header>

      <div className="hansard-search__search-row">
        <form className="hansard-search__form" onSubmit={onQSubmit} role="search">
          <label className="hansard-search__label" htmlFor="hansard-search-input">
            Search speeches
          </label>
          <input
            id="hansard-search-input"
            type="search"
            className="hansard-search__input"
            placeholder='e.g. "carbon pricing policy"'
            value={qDraft}
            onChange={(e) => onQChange(e.target.value)}
            autoFocus
          />
        </form>
        {enabled && <SaveSearchButton filter={filter} />}
      </div>

      <PoliticianPinChips
        ids={pinnedIds}
        onAdd={togglePin}
        onRemove={togglePin}
        onClearAll={clearPins}
      />

      <SpeechFilters value={filter} onChange={applyPatch} />

      <div className="hansard-search__tab-row">
        <div
          className="hansard-search__view-tabs"
          role="tablist"
          aria-label="Result view"
        >
          <button
            type="button"
            role="tab"
            aria-selected={view === "timeline"}
            className={
              "hansard-search__view-tab" +
              (view === "timeline" ? " hansard-search__view-tab--active" : "")
            }
            onClick={() => setView("timeline")}
          >
            Timeline
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "politician"}
            className={
              "hansard-search__view-tab" +
              (view === "politician" ? " hansard-search__view-tab--active" : "")
            }
            onClick={() => setView("politician")}
            title="Group results by politician to see each speaker's statements on the topic side-by-side"
          >
            By politician
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "analysis"}
            className={
              "hansard-search__view-tab" +
              (view === "analysis" ? " hansard-search__view-tab--active" : "")
            }
            onClick={() => setView("analysis")}
            title="See charts summarising who, what, and when for this search"
          >
            Analysis
          </button>
        </div>

        {view === "politician" && (
          <div
            className="politician-sort-chips"
            role="tablist"
            aria-label="Sort politicians by"
          >
            {POLITICIAN_SORTS.map((s) => {
              const active = (filter.sort ?? "mentions") === s;
              return (
                <button
                  key={s}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={
                    "politician-sort-chips__chip" +
                    (active ? " politician-sort-chips__chip--active" : "")
                  }
                  onClick={() => applyPatch({ sort: s, page: 1 })}
                >
                  {SORT_LABELS[s]}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {view === "analysis" && (
        <SearchDashboard
          filter={filter}
          enabled={enabled}
          totalMatches={dashboardTotal}
          defaultOpen
        />
      )}

      <div className="hansard-search__results">
        {!enabled && view !== "analysis" && (
          <p className="hansard-search__hint">
            {view === "politician"
              ? "Type a search query above to group results by politician."
              : "Type a phrase above or set a filter to start searching."}
          </p>
        )}

        {view === "analysis" && !enabled && (
          <p className="hansard-search__hint">
            Type a search query above to see analysis charts.
          </p>
        )}

        {enabled && loading && !data && <p className="hansard-search__hint">Searching…</p>}

        {error && (
          <p className="hansard-search__error" role="alert">
            Couldn't run that search: {error.message}
          </p>
        )}

        {enabled && view === "timeline" && timeline && timeline.items.length === 0 && !loading && (
          <p className="hansard-search__hint">No speeches match these filters.</p>
        )}

        {enabled && view === "politician" && grouped && grouped.groups.length === 0 && !loading && (
          <p className="hansard-search__hint">
            No politicians matched this query.{" "}
            <button
              type="button"
              className="hansard-search__link-button"
              onClick={() => setView("timeline")}
            >
              Switch to Timeline
            </button>{" "}
            to see individual results, including unresolved speakers.
          </p>
        )}

        {view === "timeline" && timeline && timeline.items.length > 0 && (
          <>
            <div className="hansard-search__summary">
              {timeline.totalCapped ? `1,000+ matches` : `${total.toLocaleString()} ${total === 1 ? "match" : "matches"}`}
              {timeline.mode === "semantic" ? " · ranked by similarity" : " · most recent first"}
            </div>

            <ol className="hansard-search__list" aria-label="Search results">
              {timeline.items.map((item) => (
                <li key={item.chunk_id} className="hansard-search__item">
                  <SpeechResultCard item={item} />
                </li>
              ))}
            </ol>

            {pages > 1 && (
              <nav className="hansard-search__pager" aria-label="Pagination">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => applyPatch({ page: Math.max(1, page - 1) })}
                >
                  ← Previous
                </button>
                <span className="hansard-search__pager-label">
                  Page {page} of {pages}
                  {timeline.totalCapped ? "+" : ""}
                </span>
                <button
                  type="button"
                  disabled={page >= pages}
                  onClick={() => applyPatch({ page: page + 1 })}
                >
                  Next →
                </button>
              </nav>
            )}
          </>
        )}

        {view === "politician" && grouped && grouped.groups.length > 0 && (
          <>
            <PoliticianQuickNav
              groups={grouped.groups}
              sort={filter.sort ?? "mentions"}
              pinnedIds={pinnedSet}
              onTogglePin={togglePin}
              pinCapReached={pinCapReached}
            />

            {/*
             * Anon-user advert: surface the gated "expand any card to
             * read all that politician's quotes" feature so it's visible
             * before the user has to click into a card to discover it.
             * Only renders when (a) accounts are enabled server-side,
             * (b) the visitor is signed out, and (c) at least one card
             * actually has more quotes than its initial 5 — otherwise
             * the CTA promises something the page can't deliver.
             */}
            {!user && !authDisabled && grouped.groups.some(g => g.mention_count > g.chunks.length) && (
              <div className="hansard-search__expand-advert" role="note">
                <span className="hansard-search__expand-advert-body">
                  <strong>Signed-in users can expand any card</strong> to read every matching quote
                  from that politician — not just the top 5.
                </span>
                <Link
                  to={`/login?from=${encodeURIComponent(location.pathname + location.search)}`}
                  className="hansard-search__expand-advert-cta"
                >
                  Sign in to unlock →
                </Link>
              </div>
            )}

            <div className="hansard-search__summary">
              {grouped.total_politicians} {grouped.total_politicians === 1 ? "politician" : "politicians"}
              {" · "}
              {SORT_DESCRIPTORS[filter.sort ?? "mentions"]}
              {" · oldest quote first within each card"}
            </div>

            <ol className="hansard-search__groups" aria-label="Politicians with matching speeches">
              {grouped.groups.map((g) => {
                // Key on politician id + everything that changes what
                // "matching quotes for this politician" means. Ensures
                // any expanded card with cached pageData invalidates
                // when the parent filter shifts underneath it.
                const cardKey = [
                  g.politician.id,
                  filter.q ?? "",
                  filter.lang ?? "",
                  filter.level ?? "",
                  filter.province_territory ?? "",
                  filter.party ?? "",
                  filter.from ?? "",
                  filter.to ?? "",
                  filter.exclude_presiding ? "1" : "0",
                ].join("|");
                return (
                <li key={cardKey} className="hansard-search__group-item">
                  <PoliticianResultGroup
                    group={g}
                    parentFilter={filter}
                    footer={
                      <AIContradictionAnalysis
                        politicianId={g.politician.id}
                        politicianName={g.politician.name ?? "this politician"}
                        query={filter.q ?? ""}
                        chunks={g.chunks}
                        meta={aiMeta}
                        reportsMeta={reportsMeta}
                        actionSlot={
                          <AIFullReportButton
                            politicianId={g.politician.id}
                            query={filter.q ?? ""}
                            meta={reportsMeta}
                          />
                        }
                      />
                    }
                  />
                </li>
                );
              })}
            </ol>

            <nav className="hansard-search__pager" aria-label="Pagination">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => applyPatch({ page: Math.max(1, page - 1) })}
              >
                ← Previous
              </button>
              <span className="hansard-search__pager-label">Page {page}</span>
              <button
                type="button"
                disabled={grouped.groups.length < grouped.limit}
                onClick={() => applyPatch({ page: page + 1 })}
              >
                Next →
              </button>
            </nav>
          </>
        )}
      </div>
    </section>
  );
}
