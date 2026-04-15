import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../api";

export interface PostalScanSummary {
  websites: number;
  canadian: number;
  cdn: number;
  us: number;
  foreign: number;
}

export interface PostalSiteRow {
  url: string; hostname: string;
  tier: number | null; provider: string | null; country: string | null; city: string | null;
}

export interface PostalRep {
  politician_id: string | null;
  name: string;
  district: string;
  elected_office: string;
  party: string | null;
  photo_url: string | null;
  in_database: boolean;
  scan_summary: PostalScanSummary | null;
  sites: PostalSiteRow[];
}

export interface PostalLookupResponse {
  postal_code: string;
  representatives: PostalRep[];
}

const POSTAL_RE = /^[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d$/;

interface Props {
  /** Currently-displayed postal code so the form can show its Clear button without owning the result state. */
  activePostalCode?: string | null;
  /** If provided (e.g. via ?postal= URL param from the lander), pre-fills
   *  the input and auto-submits once on mount. */
  autoSubmitCode?: string | null;
  /** Called whenever a lookup completes (or is cleared). The parent owns
   *  both the politician-ID filter for the map AND the drawer-rendered
   *  results panel — this bar is just the search field. */
  onResult: (
    response: PostalLookupResponse | null,
    politicianIds: string[] | null
  ) => void;
}

/**
 * Postal-code search FORM. No inline result panel — results render in
 * the right-side drawer (PostalResultsDrawer), same slot as the party
 * report card.
 */
export function PostalLookupBar({ activePostalCode, autoSubmitCode, onResult }: Props) {
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const autoSubmittedRef = useRef<string | null>(null);

  async function runLookup(raw: string) {
    const trimmed = raw.trim();
    if (!POSTAL_RE.test(trimmed)) {
      setError("Enter a valid Canadian postal code (e.g. K1A 0A6)");
      return;
    }
    setError(null); setLoading(true);
    try {
      const res = await fetchJson<PostalLookupResponse>(
        `/lookup/postcode/${trimmed.replace(/\s|-/g, "").toUpperCase()}`
      );
      const ids = res.representatives.map(r => r.politician_id).filter((x): x is string => !!x);
      onResult(res, ids.length ? ids : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lookup failed");
      onResult(null, null);
    } finally {
      setLoading(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    await runLookup(code);
  }

  // Pre-fill + auto-submit from a query-param driven entry (e.g. the lander's
  // "Find your data" button). Guarded by a ref so a hash-change-only URL
  // update doesn't retrigger the lookup.
  useEffect(() => {
    if (!autoSubmitCode) return;
    if (autoSubmittedRef.current === autoSubmitCode) return;
    autoSubmittedRef.current = autoSubmitCode;
    setCode(autoSubmitCode);
    void runLookup(autoSubmitCode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSubmitCode]);

  function clear() {
    setCode("");
    setError(null);
    onResult(null, null);
  }

  return (
    <section className="postal-bar">
      <form onSubmit={submit} className="postal-bar__form">
        <label className="postal-bar__label">
          <span className="postal-bar__icon">📍</span>
          Find Your Data
        </label>
        <input
          type="text"
          placeholder="Postal code  (K1A 0A6)"
          value={code}
          onChange={e => setCode(e.target.value)}
          aria-label="Canadian postal code"
          maxLength={7}
        />
        <button type="submit" disabled={loading || code.trim().length < 6}>
          {loading ? "…" : "Look up"}
        </button>
        {activePostalCode && (
          <button type="button" className="postal-bar__clear" onClick={clear}>
            Clear
          </button>
        )}
      </form>
      {error && <div className="postal-bar__error">{error}</div>}
    </section>
  );
}
