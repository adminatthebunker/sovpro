import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  fetchPoliticianQuotes,
  type GroupedSearchChunk,
  type PoliticianSearchGroup,
  type SpeechSearchFilter,
  type SpeechSearchItem,
  type TimelineSearchResponse,
} from "../hooks/useSpeechSearch";
import { useUserAuth } from "../hooks/useUserAuth";
import { UserAuthDisabledError, UserUnauthorizedError } from "../api";
import { SpeechResultCard } from "./SpeechResultCard";

const PAGE_SIZE = 50;

/** Similarity-threshold options surfaced in the filter bar. Values are
 *  cosine similarity floors; the API returns items in distance-ascending
 *  order, so high-similarity items cluster on early pages — meaning a
 *  "≥80%" filter on page 1 catches almost all qualifying quotes for most
 *  politicians, even though filtering is page-local.
 *
 *  The 0-value option means "no extra UI filter" — the API itself floors
 *  at 0.45 (matching grouped mode's MIN_SIMILARITY), so even "All matches"
 *  still drops noise below ~45% similarity. Options below 0.45 would be
 *  meaningless against that floor and are omitted. */
const SIMILARITY_OPTIONS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 0,    label: "All matches" },
  { value: 0.5,  label: "≥ 50% match" },
  { value: 0.6,  label: "≥ 60% match" },
  { value: 0.7,  label: "≥ 70% match" },
  { value: 0.8,  label: "≥ 80% match" },
];

/** English ordinal suffix: 1st, 2nd, 3rd, 4th … 11th, 12th, 13th, 21st. */
function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1: return `${n}st`;
    case 2: return `${n}nd`;
    case 3: return `${n}rd`;
    default: return `${n}th`;
  }
}

/** Rebuild a full SpeechSearchItem from a grouped chunk by re-attaching the
 *  politician from the group header. This lets us reuse SpeechResultCard
 *  with hideSpeaker=true — the card's share-target builder still needs
 *  item.politician.name for the attribution, so it can't just be null. */
function rehydrate(
  chunk: GroupedSearchChunk,
  politician: PoliticianSearchGroup["politician"],
): SpeechSearchItem {
  return {
    chunk_id: chunk.chunk_id,
    speech_id: chunk.speech_id,
    chunk_index: chunk.chunk_index,
    text: chunk.text,
    snippet_html: chunk.snippet_html,
    similarity: chunk.similarity,
    spoken_at: chunk.spoken_at,
    language: chunk.language,
    level: chunk.level,
    province_territory: chunk.province_territory,
    party_at_time: chunk.party_at_time,
    politician,
    speech: chunk.speech,
  };
}

export interface PoliticianResultGroupProps {
  group: PoliticianSearchGroup;
  /** The parent search's filter. The expand fetch reuses these constraints
   *  so "all quotes" stays scoped to the same date / level / language slice
   *  the user is already looking at. Required for the signed-in deep-dive
   *  to be coherent with the parent search. */
  parentFilter: SpeechSearchFilter;
  /** Optional slot rendered below the chunk list. Used by the grouped
   *  view on /search to mount the per-card AI contradictions section.
   *  Kept as a generic ReactNode so this component stays UI-pure and
   *  the caller decides which cards get the button (e.g. filter to
   *  cards spanning ≥2 parliaments later). */
  footer?: ReactNode;
}

type ExpandError =
  | { kind: "session-expired" }
  | { kind: "accounts-disabled" }
  | { kind: "rate-limited" }
  | { kind: "other"; message: string };

export function PoliticianResultGroup({ group, parentFilter, footer }: PoliticianResultGroupProps) {
  const { politician, chunks, best_similarity, avg_similarity, mention_count, keyword_hits } = group;
  const { user, disabled } = useUserAuth();
  const location = useLocation();

  // Two distinct viewing modes for the same card:
  //   collapsed → the original 5 chunks, sorted chronologically (existing UX)
  //   expanded  → page-paginated full list, ranked by semantic distance,
  //               with a client-side filter bar at the top
  //
  // Page state is preserved across collapse/expand so the user can
  // glance back at the top-5 view and resume where they were. Filter
  // state (similarity floor + keyword) is reset on collapse so re-opens
  // start clean.
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(1);
  const [pageData, setPageData] = useState<TimelineSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ExpandError | null>(null);
  const [simThreshold, setSimThreshold] = useState(0);
  const [keyword, setKeyword] = useState("");

  // Use the server's mention_count (all qualified chunks above the
  // similarity threshold) rather than chunks.length, which is capped at
  // per_group_limit=5 and would undercount prolific speakers.
  const displayCount = mention_count ?? chunks.length;
  // True only when there's actually more to expand — hides the affordance
  // entirely on cards where the initial 5 already covers everything.
  const hasMoreThanShown = (mention_count ?? chunks.length) > chunks.length;

  // Collapsed-view chunks: the existing top-5 from the grouped search,
  // already in chronological order from the API.
  const collapsedChunks = chunks;
  const parliaments = new Set<number>();
  for (const c of collapsedChunks) {
    if (c.speech.session) parliaments.add(c.speech.session.parliament_number);
  }
  const parliamentCount = parliaments.size;

  // Similarity is now server-side (min_similarity query param), so the
  // current page's items are already above the chosen floor. Only the
  // keyword filter runs client-side — cheap, instant feedback, and
  // scoped to what's on the visible page (no DB round-trip per
  // keystroke).
  const filteredItems = useMemo(() => {
    if (!pageData) return [] as SpeechSearchItem[];
    const kw = keyword.trim().toLowerCase();
    if (!kw) return pageData.items;
    return pageData.items.filter(item => (item.text ?? "").toLowerCase().includes(kw));
  }, [pageData, keyword]);

  // Parliament divider is only meaningful in the chronological collapsed
  // view. The expanded list is ranked by distance, where adjacent chunks
  // can flip parliaments arbitrarily — dividers there would be noise.
  let lastParliament: number | null = null;

  function mapError(e: unknown): ExpandError {
    if (e instanceof UserUnauthorizedError) return { kind: "session-expired" };
    if (e instanceof UserAuthDisabledError) return { kind: "accounts-disabled" };
    if (e instanceof Error && /^429\b/.test(e.message)) return { kind: "rate-limited" };
    return { kind: "other", message: e instanceof Error ? e.message : "Couldn't load quotes" };
  }

  async function fetchPage(targetPage: number, thresholdOverride?: number) {
    setLoading(true);
    setError(null);
    try {
      const threshold = thresholdOverride !== undefined ? thresholdOverride : simThreshold;
      const res = await fetchPoliticianQuotes(
        politician.id,
        parentFilter,
        targetPage,
        { limit: PAGE_SIZE, minSimilarity: threshold > 0 ? threshold : undefined },
      );
      setPageData(res);
      setPage(res.page);
    } catch (e) {
      setError(mapError(e));
    } finally {
      setLoading(false);
    }
  }

  async function showAll() {
    setExpanded(true);
    // Re-use cached page data when re-expanding without a filter change;
    // only refetch when we have nothing or the parent filter has shifted
    // such that the cached page is stale (handled by card remount key
    // in HansardSearchPage).
    if (!pageData) {
      await fetchPage(1);
    }
  }

  function goPrev() {
    if (loading || page <= 1) return;
    fetchPage(page - 1);
  }

  function goNext() {
    if (loading) return;
    if (!pageData || page >= pageData.pages) return;
    fetchPage(page + 1);
  }

  // Similarity changes reshape what the server returns (total, pages,
  // per-page chunk set), so we jump back to page 1 and refetch with the
  // new threshold. Keyword stays client-side — no refetch for those.
  function onSimChange(next: number) {
    setSimThreshold(next);
    fetchPage(1, next);
  }

  function collapse() {
    setExpanded(false);
    // Reset filter state so a future re-expand starts clean. If we
    // were on a tight similarity threshold, re-opening will refetch
    // page 1 at the default (server floor) via showAll → pageData
    // being stale wrt simThreshold=0. Simpler: clear pageData too so
    // next expand is always fresh.
    if (simThreshold > 0) {
      setSimThreshold(0);
      setPageData(null);
      setPage(1);
    }
    setKeyword("");
  }

  // Whether the per-card expand affordance should appear at all.
  // Suppressed when accounts are disabled server-side (mirrors
  // SaveSearchButton's posture) or when there's nothing more to show.
  const showExpand = !disabled && hasMoreThanShown;

  // Collapsed-mode affordance: anon CTA OR signed-in "Show all" button.
  // Rendered only when not currently expanded.
  let collapsedExpandAffordance: ReactNode = null;
  if (showExpand && !expanded) {
    if (!user) {
      const from = encodeURIComponent(location.pathname + location.search);
      const name = politician.name ?? "this politician";
      collapsedExpandAffordance = (
        <Link
          to={`/login?from=${from}`}
          className="politician-group__expand-cta"
        >
          Sign in to read all of {name}'s matching quotes →
        </Link>
      );
    } else {
      collapsedExpandAffordance = (
        <button
          type="button"
          className="politician-group__expand-button"
          onClick={showAll}
          disabled={loading}
        >
          {loading ? "Loading…" : "Show all matching quotes"}
        </button>
      );
    }
  }

  let errorBlock: ReactNode = null;
  if (error) {
    if (error.kind === "session-expired") {
      const from = encodeURIComponent(location.pathname + location.search);
      errorBlock = (
        <p className="politician-group__expand-error" role="alert">
          Session expired. <Link to={`/login?from=${from}`}>Sign in again</Link> to load quotes.
        </p>
      );
    } else if (error.kind === "rate-limited") {
      errorBlock = (
        <p className="politician-group__expand-error" role="alert">
          Slow down — too many expand requests. Try again in a moment.
        </p>
      );
    } else if (error.kind === "accounts-disabled") {
      errorBlock = (
        <p className="politician-group__expand-error" role="alert">
          Accounts are temporarily disabled.
        </p>
      );
    } else {
      errorBlock = (
        <p className="politician-group__expand-error" role="alert">
          Couldn't load quotes: {error.message}
        </p>
      );
    }
  }

  // Any user-applied tightening beyond defaults. Similarity is
  // server-side now, so when it's > 0 the pageData already reflects
  // the narrower set. Keyword is still client-side.
  const keywordActive = keyword.trim().length > 0;
  const filteringActive = simThreshold > 0 || keywordActive;
  const totalLabel = pageData
    ? (pageData.totalCapped ? `${pageData.total.toLocaleString()}+` : pageData.total.toLocaleString())
    : null;

  // Header stats are reactive to filters in expanded mode so the count
  // next to the politician's name matches what the user is actually
  // looking at. Falls back to the server-provided group stats when
  // collapsed (or before pageData arrives).
  let headerCountText: string;
  let headerCountSuffix = "";
  let headerBest = best_similarity;
  let headerAvg = avg_similarity;
  let headerKeywordHits: number | null = keyword_hits ?? 0;
  let headerParliamentNote: string | null =
    parliamentCount > 1 ? ` across ${parliamentCount} parliaments` : null;

  if (expanded && pageData) {
    if (keywordActive) {
      // Keyword is page-local (client-side filter over what the server
      // already returned); label explicitly so the count doesn't imply
      // a whole-result-set number.
      const n = filteredItems.length;
      headerCountText = `${n.toLocaleString()} ${n === 1 ? "quote" : "quotes"} on this page`;
      headerCountSuffix = "";
      const sims = filteredItems
        .map(i => i.similarity)
        .filter((s): s is number => typeof s === "number");
      if (sims.length > 0) {
        headerBest = Math.max(...sims);
        headerAvg = sims.reduce((a, b) => a + b, 0) / sims.length;
      } else {
        headerBest = null;
        headerAvg = null;
      }
      headerKeywordHits = null;
      headerParliamentNote = null;
    } else {
      // Similarity-only (or no extra filter) — pageData.total is the
      // true count for the current threshold, so show it as the
      // headline. This is the fix for "expanded view says 1,000+ but
      // the politician hasn't actually said 1,000 things about the
      // query": the server threshold makes `total` the honest number.
      headerCountText = `${pageData.total.toLocaleString()}${pageData.totalCapped ? "+" : ""} ${pageData.total === 1 ? "quote" : "quotes"}`;
      headerCountSuffix = "";
      // If a similarity floor is active, recompute avg/best from the
      // currently visible chunks (still representative — they're all
      // above the threshold).
      if (simThreshold > 0) {
        const sims = pageData.items
          .map(i => i.similarity)
          .filter((s): s is number => typeof s === "number");
        if (sims.length > 0) {
          headerBest = Math.max(...sims);
          headerAvg = sims.reduce((a, b) => a + b, 0) / sims.length;
        }
      }
      headerKeywordHits = null;
      headerParliamentNote = null;
    }
  } else {
    // Collapsed: same display as before.
    headerCountText = `${displayCount.toLocaleString()} ${displayCount === 1 ? "quote" : "quotes"}`;
  }

  return (
    <article
      className="politician-group"
      id={`pg-card-${politician.id}`}
      aria-labelledby={`pg-${politician.id}`}
    >
      <header className="politician-group__header">
        {politician.photo_url ? (
          <img
            src={politician.photo_url}
            alt=""
            className="politician-group__photo"
            loading="lazy"
            width={56}
            height={56}
          />
        ) : (
          <div
            className="politician-group__photo politician-group__photo--placeholder"
            aria-hidden="true"
          >
            {(politician.name ?? "?").slice(0, 1)}
          </div>
        )}
        <div className="politician-group__meta">
          <Link
            to={`/politicians/${politician.id}`}
            className="politician-group__name"
            id={`pg-${politician.id}`}
          >
            {politician.name ?? "Unknown"}
          </Link>
          <span className="politician-group__sub">
            {politician.party ?? "—"}
            {" · "}
            <span className="politician-group__count">
              {headerCountText}{headerCountSuffix}
              {headerParliamentNote}
            </span>
          </span>
          <span
            className="politician-group__secondary-metrics"
            aria-label="Match statistics"
          >
            {headerAvg != null && (
              <span
                className="politician-group__secondary-metric"
                title={
                  expanded && filteringActive
                    ? "Average cosine similarity across the filtered quotes on this page"
                    : "Average cosine similarity across qualifying chunks"
                }
              >
                avg {(headerAvg * 100).toFixed(0)}%
              </span>
            )}
            {headerKeywordHits != null && (
              <span
                className="politician-group__secondary-metric"
                title="Chunks that also match the keyword query literally"
              >
                {headerKeywordHits} keyword hit{headerKeywordHits === 1 ? "" : "s"}
              </span>
            )}
          </span>
        </div>
        {headerBest !== null && (
          <span
            className="politician-group__similarity"
            title={
              expanded && filteringActive
                ? "Strongest cosine similarity across the filtered quotes on this page"
                : "Strongest cosine similarity across this politician's matching chunks"
            }
          >
            {(headerBest * 100).toFixed(0)}% best
          </span>
        )}
      </header>

      {!expanded && (
        <ol className="politician-group__chunks" aria-label={`Top quotes by ${politician.name ?? "this politician"}`}>
          {collapsedChunks.map((chunk) => {
            const thisParl = chunk.speech.session?.parliament_number ?? null;
            const showDivider =
              lastParliament !== null && thisParl !== null && lastParliament !== thisParl;
            const dividerLabel = thisParl !== null ? `${ordinal(thisParl)} Parliament` : null;
            lastParliament = thisParl;
            return (
              <li key={chunk.chunk_id} className="politician-group__chunk">
                {showDivider && dividerLabel && (
                  <div className="politician-group__parl-divider" aria-hidden="true">
                    <span>{dividerLabel}</span>
                  </div>
                )}
                <SpeechResultCard item={rehydrate(chunk, politician)} hideSpeaker />
              </li>
            );
          })}
        </ol>
      )}

      {expanded && (
        <div className="politician-group__expanded">
          <div
            className="politician-group__filter-bar"
            role="group"
            aria-label={`Filter quotes by ${politician.name ?? "this politician"}`}
          >
            <span className="politician-group__filter-title">
              Filter quotes by <strong>{politician.name ?? "this politician"}</strong>
              {totalLabel != null && (
                <span className="politician-group__filter-total">
                  {" · "}
                  {totalLabel} total
                </span>
              )}
            </span>
            <div className="politician-group__filter-controls">
              <label className="politician-group__filter-item">
                <span className="politician-group__filter-label">Min match</span>
                <select
                  value={simThreshold}
                  onChange={e => onSimChange(Number(e.target.value))}
                  disabled={loading}
                >
                  {SIMILARITY_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
              <label className="politician-group__filter-item politician-group__filter-item--keyword">
                <span className="politician-group__filter-label">Keyword</span>
                <input
                  type="search"
                  value={keyword}
                  onChange={e => setKeyword(e.target.value)}
                  placeholder="Filter this page…"
                  disabled={!pageData || loading}
                />
              </label>
              {filteringActive && (
                <button
                  type="button"
                  className="politician-group__filter-clear"
                  onClick={() => {
                    setKeyword("");
                    if (simThreshold > 0) onSimChange(0);
                  }}
                >
                  Clear filters
                </button>
              )}
            </div>
            {pageData && keywordActive && (
              <span className="politician-group__filter-count" aria-live="polite">
                {filteredItems.length} of {pageData.items.length} on this page
              </span>
            )}
          </div>

          {loading && !pageData && (
            <p className="politician-group__expand-loading">Loading quotes…</p>
          )}

          {pageData && filteredItems.length === 0 && (
            <p className="politician-group__expand-empty">
              {filteringActive
                ? "No quotes on this page match your filters. Try lowering the similarity threshold or clearing the keyword."
                : "No quotes on this page."}
            </p>
          )}

          {pageData && filteredItems.length > 0 && (
            <ol
              className="politician-group__chunks politician-group__chunks--expanded"
              aria-label={`All matching quotes by ${politician.name ?? "this politician"}`}
            >
              {filteredItems.map(item => (
                <li key={item.chunk_id} className="politician-group__chunk">
                  <SpeechResultCard item={item} hideSpeaker />
                </li>
              ))}
            </ol>
          )}

          {pageData && pageData.pages > 1 && (
            <nav className="politician-group__pager" aria-label="Quote pagination">
              <button
                type="button"
                className="politician-group__pager-btn"
                onClick={goPrev}
                disabled={loading || page <= 1}
              >
                ← Previous
              </button>
              <span className="politician-group__pager-label">
                {loading ? "Loading…" : `Page ${page} of ${pageData.pages}${pageData.totalCapped ? "+" : ""}`}
              </span>
              <button
                type="button"
                className="politician-group__pager-btn"
                onClick={goNext}
                disabled={loading || page >= pageData.pages}
              >
                Next →
              </button>
            </nav>
          )}

          <div className="politician-group__expand-foot">
            <button
              type="button"
              className="politician-group__collapse-button"
              onClick={collapse}
            >
              ← Collapse to top 5
            </button>
          </div>
        </div>
      )}

      {collapsedExpandAffordance && (
        <div className="politician-group__expand">{collapsedExpandAffordance}</div>
      )}
      {errorBlock}
      {footer && <div className="politician-group__footer">{footer}</div>}
    </article>
  );
}
