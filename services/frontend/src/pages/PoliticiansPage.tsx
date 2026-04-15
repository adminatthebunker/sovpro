import { useMemo, useState } from "react";
import { PoliticianCard } from "../components/PoliticianCard";
import { usePoliticians, type PoliticiansFilter } from "../hooks/usePoliticians";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useFetch } from "../hooks/useFetch";

const PROVINCES = ["AB", "BC", "MB", "NB", "NL", "NS", "ON", "PE", "QC", "SK", "NT", "NU", "YT"];

interface PartiesResponse {
  parties: Array<{ party: string; politicians: number }>;
}

export default function PoliticiansPage() {
  useDocumentTitle("Politician Socials");
  const { data: partiesData } = useFetch<PartiesResponse>("/parties");
  const parties = partiesData?.parties ?? [];
  const [level, setLevel] = useState<PoliticiansFilter["level"]>(undefined);
  const [province, setProvince] = useState<string>("");
  const [party, setParty] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [hasTwitter, setHasTwitter] = useState(false);
  const [hasFacebook, setHasFacebook] = useState(false);
  const [hasInstagram, setHasInstagram] = useState(false);
  const [socialsLive, setSocialsLive] = useState(false);
  const [page, setPage] = useState<number>(1);

  const filter: PoliticiansFilter = useMemo(
    () => ({
      level,
      province: province || undefined,
      party: party || undefined,
      search: search || undefined,
      has_twitter: hasTwitter || undefined,
      has_facebook: hasFacebook || undefined,
      has_instagram: hasInstagram || undefined,
      socials_live: socialsLive || undefined,
      page,
      limit: 40,
    }),
    [level, province, party, search, hasTwitter, hasFacebook, hasInstagram, socialsLive, page]
  );

  const { data, loading, error } = usePoliticians(filter);

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = data?.pages ?? 1;

  const resetPage = () => setPage(1);

  return (
    <section className="politicians">
      <header className="politicians__header">
        <h2 className="politicians__title">Politician socials</h2>
        <p className="politicians__subtitle">
          Which Canadian politicians are on X, Facebook, Instagram, and beyond. Click any card for full handles, liveness, and hosting detail.
        </p>
      </header>

      <div className="politicians__filters">
        <label>
          <span>Search</span>
          <input
            type="search"
            placeholder="Name…"
            value={search}
            onChange={e => { setSearch(e.target.value); resetPage(); }}
          />
        </label>
        <label>
          <span>Level</span>
          <select
            value={level ?? ""}
            onChange={e => { setLevel((e.target.value || undefined) as PoliticiansFilter["level"]); resetPage(); }}
          >
            <option value="">All levels</option>
            <option value="federal">Federal</option>
            <option value="provincial">Provincial</option>
            <option value="municipal">Municipal</option>
          </select>
        </label>
        <label>
          <span>Province</span>
          <select
            value={province}
            onChange={e => { setProvince(e.target.value); resetPage(); }}
          >
            <option value="">All provinces</option>
            {PROVINCES.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label>
          <span>Party</span>
          <select
            value={party}
            onChange={e => { setParty(e.target.value); resetPage(); }}
          >
            <option value="">All parties</option>
            {parties.map(p => (
              <option key={p.party} value={p.party}>
                {p.party} ({p.politicians})
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="politicians__toggles" role="group" aria-label="Social presence filters">
        <label>
          <input type="checkbox" checked={hasTwitter} onChange={e => { setHasTwitter(e.target.checked); resetPage(); }} />
          <span>On X / Twitter</span>
        </label>
        <label>
          <input type="checkbox" checked={hasFacebook} onChange={e => { setHasFacebook(e.target.checked); resetPage(); }} />
          <span>On Facebook</span>
        </label>
        <label>
          <input type="checkbox" checked={hasInstagram} onChange={e => { setHasInstagram(e.target.checked); resetPage(); }} />
          <span>On Instagram</span>
        </label>
        <label>
          <input type="checkbox" checked={socialsLive} onChange={e => { setSocialsLive(e.target.checked); resetPage(); }} />
          <span>Live handles only</span>
        </label>
      </div>

      <p className="politicians__count">
        {loading && !data
          ? "Loading…"
          : total === 0
            ? "No politicians match these filters."
            : `${total.toLocaleString()} politician${total === 1 ? "" : "s"}${search ? ` matching "${search}"` : ""}.`}
      </p>

      {error && <div className="mapview__error">Failed to load: {error.message}</div>}

      <div className="politicians__grid">
        {items.map(p => (
          <PoliticianCard key={p.id} politician={p} />
        ))}
      </div>

      {pages > 1 && (
        <nav className="politicians__pagination" aria-label="Pagination">
          <button disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}>← Previous</button>
          <span>Page {page} of {pages}</span>
          <button disabled={page >= pages} onClick={() => setPage(p => Math.min(pages, p + 1))}>Next →</button>
        </nav>
      )}
    </section>
  );
}
