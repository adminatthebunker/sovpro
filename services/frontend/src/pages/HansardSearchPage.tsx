import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import {
  useSpeechSearch,
  useSpeechSearchMeta,
  type PoliticianSort,
  type SpeechSearchFilter,
} from "../hooks/useSpeechSearch";
import { SpeechFilters } from "../components/SpeechFilters";
import { SpeechResultCard } from "../components/SpeechResultCard";
import { SearchDashboard } from "../components/SearchDashboard";
import { SaveSearchButton } from "../components/SaveSearchButton";
import { PoliticianResultGroup } from "../components/PoliticianResultGroup";
import { PoliticianQuickNav } from "../components/PoliticianQuickNav";
import { AIContradictionAnalysis } from "../components/AIContradictionAnalysis";
import { useAIAnalyzeMeta } from "../hooks/useAIAnalyzeMeta";
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

function readFilter(params: URLSearchParams): SpeechSearchFilter {
  const lang = params.get("lang");
  const level = params.get("level");
  const view = readView(params);
  return {
    q: params.get("q") ?? "",
    lang: (lang === "en" || lang === "fr" || lang === "any" ? lang : "any") as SpeechSearchFilter["lang"],
    level: (level === "federal" || level === "provincial" || level === "municipal"
      ? level
      : undefined) as SpeechSearchFilter["level"],
    province_territory: params.get("province") ?? undefined,
    party: params.get("party") ?? undefined,
    from: params.get("from") ?? undefined,
    to: params.get("to") ?? undefined,
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

  const hasAnyFilter = Boolean(
    filter.level || filter.province_territory || filter.party || filter.from || filter.to,
  );
  const hasQuery = Boolean(filter.q && filter.q.trim());
  // Grouped mode is semantic-only (the API 400s on a q-less grouped call).
  // Timeline still allows filter-only searches.
  const enabled = view === "politician" ? hasQuery : (hasQuery || hasAnyFilter);
  const { data, loading, error } = useSpeechSearch(filter, enabled);
  const meta = useSpeechSearchMeta();
  // Single meta fetch for the whole page (cached module-level), so
  // rendering 20 grouped cards doesn't produce 20 /contradictions/meta
  // calls. Only gets used on the politician view.
  const { meta: aiMeta } = useAIAnalyzeMeta();

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

      <SpeechFilters value={filter} onChange={applyPatch} />

      {enabled && <SaveSearchButton filter={filter} />}

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
            />

            <div className="hansard-search__summary">
              {grouped.total_politicians} {grouped.total_politicians === 1 ? "politician" : "politicians"}
              {" · "}
              {SORT_DESCRIPTORS[filter.sort ?? "mentions"]}
              {" · oldest quote first within each card"}
            </div>

            <ol className="hansard-search__groups" aria-label="Politicians with matching speeches">
              {grouped.groups.map((g) => (
                <li key={g.politician.id} className="hansard-search__group-item">
                  <PoliticianResultGroup
                    group={g}
                    footer={
                      <AIContradictionAnalysis
                        politicianId={g.politician.id}
                        politicianName={g.politician.name ?? "this politician"}
                        query={filter.q ?? ""}
                        chunks={g.chunks}
                        meta={aiMeta}
                      />
                    }
                  />
                </li>
              ))}
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
