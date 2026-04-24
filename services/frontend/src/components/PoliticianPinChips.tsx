import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../api";
import { MAX_POLITICIAN_PINS } from "../hooks/useSpeechSearch";

/**
 * Renders the currently-pinned politicians as removable chips near the
 * search bar, plus a typeahead picker to add more pins. Pins narrow all
 * search views (timeline, by-politician grid, analysis), so the picker
 * is the escape hatch: when the grid has collapsed to just the pinned
 * cards, users can still discover and add more people by name here.
 *
 * Resolves names/photos via the batched `/politicians/resolve` endpoint
 * so a user arriving via a shared URL (e.g.
 * `/search?politician_id=<uuid>&politician_id=<uuid>`) sees real names
 * rather than raw UUIDs.
 */

interface ResolvedPolitician {
  id: string;
  name: string;
  photo_url: string | null;
  slug: string | null;
}

interface SearchHit {
  id: string;
  name: string;
  photo_url: string | null;
  slug: string | null;
  party: string | null;
  level: string | null;
  province_territory: string | null;
}

interface Props {
  ids: string[];
  onAdd: (id: string) => void;
  onRemove: (id: string) => void;
  onClearAll: () => void;
}

export function PoliticianPinChips({ ids, onAdd, onRemove, onClearAll }: Props) {
  const [resolved, setResolved] = useState<Map<string, ResolvedPolitician>>(new Map());
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [open, setOpen] = useState(false);
  const debounce = useRef<number | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const atCap = ids.length >= MAX_POLITICIAN_PINS;

  useEffect(() => {
    if (ids.length === 0) {
      setResolved(new Map());
      return;
    }
    // Only resolve ids we haven't seen yet — batches accumulate, so a
    // user who adds one pin at a time doesn't re-fetch the whole set.
    const missing = ids.filter(id => !resolved.has(id));
    if (missing.length === 0) return;
    let cancelled = false;
    void fetchJson<{ items: ResolvedPolitician[] }>(
      `/politicians/resolve?ids=${missing.join(",")}`,
    ).then(res => {
      if (cancelled) return;
      setResolved(prev => {
        const next = new Map(prev);
        for (const p of res.items) next.set(p.id, p);
        return next;
      });
    }).catch(() => {
      // Silent degradation — the UUID is fine as a fallback label.
    });
    return () => {
      cancelled = true;
    };
  }, [ids, resolved]);

  // Debounced typeahead against /politicians/search. Fires at 2+ chars,
  // 200ms after last keystroke — snappy enough to feel instant without
  // hammering the DB.
  useEffect(() => {
    if (debounce.current) window.clearTimeout(debounce.current);
    const q = query.trim();
    if (q.length < 2) {
      setHits([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    debounce.current = window.setTimeout(() => {
      void fetchJson<{ items: SearchHit[] }>(
        `/politicians/search?q=${encodeURIComponent(q)}`,
      ).then(res => {
        setHits(res.items ?? []);
      }).catch(() => {
        setHits([]);
      }).finally(() => setSearching(false));
    }, 200);
    return () => {
      if (debounce.current) window.clearTimeout(debounce.current);
    };
  }, [query]);

  // Click-outside closes the dropdown.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const onPick = (id: string) => {
    onAdd(id);
    setQuery("");
    setHits([]);
    setOpen(false);
  };

  const visibleHits = hits.filter(h => !ids.includes(h.id));

  // Render the picker even when there are no pins yet — makes "pin a
  // politician by name" discoverable from the search page without
  // hunting through the by-politician grid first.
  return (
    <div ref={rootRef} className="cpd-pin-chips" role="group" aria-label="Politicians pinned to this search">
      {ids.length > 0 && <span className="cpd-pin-chips__label">Pinned:</span>}
      {ids.map(id => {
        const p = resolved.get(id);
        const label = p?.name ?? id.slice(0, 8);
        return (
          <span key={id} className="cpd-pin-chip">
            {p?.photo_url && (
              <img
                src={p.photo_url}
                alt=""
                className="cpd-pin-chip__photo"
                width={18}
                height={18}
                loading="lazy"
              />
            )}
            <span className="cpd-pin-chip__name">{label}</span>
            <button
              type="button"
              className="cpd-pin-chip__remove"
              onClick={() => onRemove(id)}
              aria-label={`Remove ${label} pin`}
              title={`Remove ${label}`}
            >
              ×
            </button>
          </span>
        );
      })}
      {ids.length > 1 && (
        <button
          type="button"
          className="cpd-pin-chips__clear"
          onClick={onClearAll}
          title="Clear all politician pins"
        >
          Clear all
        </button>
      )}

      <div className="cpd-pin-picker">
        <input
          type="text"
          className="cpd-pin-picker__input"
          placeholder={atCap ? `Pin limit reached (${MAX_POLITICIAN_PINS})` : "+ pin a politician by name…"}
          value={query}
          onChange={e => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          disabled={atCap}
          aria-label="Search politicians to pin"
        />
        {open && query.trim().length >= 2 && (
          <ul className="cpd-pin-picker__results" role="listbox">
            {searching && visibleHits.length === 0 && (
              <li className="cpd-pin-picker__note">Searching…</li>
            )}
            {!searching && visibleHits.length === 0 && (
              <li className="cpd-pin-picker__note">No matches.</li>
            )}
            {visibleHits.map(h => (
              <li key={h.id}>
                <button
                  type="button"
                  className="cpd-pin-picker__hit"
                  onClick={() => onPick(h.id)}
                  role="option"
                  aria-selected="false"
                >
                  {h.photo_url ? (
                    <img
                      src={h.photo_url}
                      alt=""
                      width={22}
                      height={22}
                      className="cpd-pin-picker__photo"
                      loading="lazy"
                    />
                  ) : (
                    <span className="cpd-pin-picker__photo cpd-pin-picker__photo--placeholder" aria-hidden="true">
                      {h.name.slice(0, 1)}
                    </span>
                  )}
                  <span className="cpd-pin-picker__name">{h.name}</span>
                  {(h.party || h.province_territory) && (
                    <span className="cpd-pin-picker__meta">
                      {[h.party, h.province_territory].filter(Boolean).join(" · ")}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
