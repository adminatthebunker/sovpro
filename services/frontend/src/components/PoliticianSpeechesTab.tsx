import { useEffect, useRef, useState } from "react";
import { useSpeechSearch, type SpeechSearchFilter } from "../hooks/useSpeechSearch";
import { SpeechResultCard } from "./SpeechResultCard";

export interface PoliticianSpeechesTabProps {
  politicianId: string;
}

export function PoliticianSpeechesTab({ politicianId }: PoliticianSpeechesTabProps) {
  const [qDraft, setQDraft] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const debounceTimer = useRef<number | null>(null);

  // Reset paging when the query changes.
  useEffect(() => {
    setPage(1);
  }, [q]);

  const onQChange = (next: string) => {
    setQDraft(next);
    if (debounceTimer.current) window.clearTimeout(debounceTimer.current);
    debounceTimer.current = window.setTimeout(() => setQ(next), 300);
  };

  const filter: SpeechSearchFilter = {
    q: q || undefined,
    politician_id: politicianId,
    page,
    limit: 15,
  };
  const { data, loading, error } = useSpeechSearch(filter, true);

  const pages = data?.pages ?? 1;
  const total = data?.total ?? 0;

  return (
    <div className="pol-speeches">
      <div className="pol-speeches__toolbar">
        <input
          type="search"
          className="pol-speeches__input"
          placeholder="Filter this MP's speeches by topic…"
          value={qDraft}
          onChange={(e) => onQChange(e.target.value)}
          aria-label="Filter this MP's speeches"
        />
        {q && (
          <button
            type="button"
            className="pol-speeches__clear"
            onClick={() => {
              setQDraft("");
              setQ("");
            }}
          >
            Clear
          </button>
        )}
      </div>

      {loading && !data && <p className="pol-speeches__hint">Loading speeches…</p>}

      {error && (
        <p className="pol-speeches__error" role="alert">
          Couldn't load speeches: {error.message}
        </p>
      )}

      {data && data.items.length === 0 && !loading && (
        <p className="pol-speeches__hint">
          {q
            ? `No speeches match "${q}" for this politician.`
            : "No indexed speeches yet for this politician."}
        </p>
      )}

      {data && data.items.length > 0 && (
        <>
          <div className="pol-speeches__summary">
            {data.totalCapped ? "1,000+ speeches" : `${total.toLocaleString()} speech ${total === 1 ? "fragment" : "fragments"}`}
            {data.mode === "semantic" ? ` matching "${q}"` : " · most recent first"}
          </div>

          <ol className="pol-speeches__list">
            {data.items.map((item) => (
              <li key={item.chunk_id} className="pol-speeches__item">
                <SpeechResultCard item={item} hideSpeaker />
              </li>
            ))}
          </ol>

          {pages > 1 && (
            <nav className="pol-speeches__pager" aria-label="Pagination">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                ← Previous
              </button>
              <span className="pol-speeches__pager-label">
                Page {page} of {pages}
                {data.totalCapped ? "+" : ""}
              </span>
              <button
                type="button"
                disabled={page >= pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next →
              </button>
            </nav>
          )}
        </>
      )}
    </div>
  );
}
