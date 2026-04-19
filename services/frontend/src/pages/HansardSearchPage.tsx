import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import {
  useSpeechSearch,
  useSpeechSearchMeta,
  type SpeechSearchFilter,
} from "../hooks/useSpeechSearch";
import { SpeechFilters } from "../components/SpeechFilters";
import { SpeechResultCard } from "../components/SpeechResultCard";
import { SearchDashboard } from "../components/SearchDashboard";
import "../styles/hansard-search.css";

function readFilter(params: URLSearchParams): SpeechSearchFilter {
  const lang = params.get("lang");
  const level = params.get("level");
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
  };
}

function writeFilter(f: SpeechSearchFilter): URLSearchParams {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.lang && f.lang !== "any") p.set("lang", f.lang);
  if (f.level) p.set("level", f.level);
  if (f.province_territory) p.set("province", f.province_territory);
  if (f.party) p.set("party", f.party);
  if (f.from) p.set("from", f.from);
  if (f.to) p.set("to", f.to);
  if (f.page && f.page > 1) p.set("page", String(f.page));
  return p;
}

export default function HansardSearchPage() {
  useDocumentTitle("Hansard Search");
  const [searchParams, setSearchParams] = useSearchParams();
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
    setSearchParams(writeFilter(next), { replace: false });
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
  const enabled = Boolean(filter.q && filter.q.trim()) || hasAnyFilter;
  const { data, loading, error } = useSpeechSearch(filter, enabled);
  const meta = useSpeechSearchMeta();

  const page = filter.page ?? 1;
  const pages = data?.pages ?? 1;
  const total = data?.total ?? 0;

  return (
    <section className="hansard-search">
      <header className="hansard-search__header">
        <h2 className="hansard-search__title">Hansard Search</h2>
        <p className="hansard-search__subtitle">
          Semantic search over Canadian parliamentary speeches. Find what MPs said, not just the
          words they used.
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
        <input
          type="search"
          className="hansard-search__input"
          placeholder='e.g. "carbon pricing policy"'
          value={qDraft}
          onChange={(e) => onQChange(e.target.value)}
          autoFocus
          aria-label="Search speeches"
        />
      </form>

      <SpeechFilters value={filter} onChange={applyPatch} />

      <SearchDashboard filter={filter} enabled={enabled} totalMatches={data?.total} />

      <div className="hansard-search__results">
        {!enabled && (
          <p className="hansard-search__hint">
            Type a phrase above or set a filter to start searching.
          </p>
        )}

        {enabled && loading && !data && <p className="hansard-search__hint">Searching…</p>}

        {error && (
          <p className="hansard-search__error" role="alert">
            Couldn't run that search: {error.message}
          </p>
        )}

        {enabled && data && data.items.length === 0 && !loading && (
          <p className="hansard-search__hint">No speeches match these filters.</p>
        )}

        {data && data.items.length > 0 && (
          <>
            <div className="hansard-search__summary">
              {data.totalCapped ? `1,000+ matches` : `${total.toLocaleString()} ${total === 1 ? "match" : "matches"}`}
              {data.mode === "semantic" ? " · ranked by similarity" : " · most recent first"}
            </div>

            <ol className="hansard-search__list" aria-label="Search results">
              {data.items.map((item) => (
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
                  {data.totalCapped ? "+" : ""}
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
      </div>
    </section>
  );
}
