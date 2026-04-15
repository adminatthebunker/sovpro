import {
  itemsOf,
  usePoliticianTerms,
  type PoliticianTerm,
} from "../hooks/usePolitician";

interface Props {
  politicianId: string;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "present";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function yearOf(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : String(d.getFullYear());
}

export function PoliticianTermsTab({ politicianId }: Props) {
  const { data, loading, error, notFound } = usePoliticianTerms(politicianId);

  if (loading) return <div className="pol-tab__loading">Loading term history…</div>;
  if (notFound) {
    return (
      <div className="pol-tab__empty">
        <strong>Not yet available.</strong>
        <p>Term history tracking (Phase 6) hasn't finished its first snapshot
           run for this politician. Once it does, party switches, office
           changes, and retirements will all show up here as a timeline.</p>
      </div>
    );
  }
  if (error) {
    return <div className="pol-tab__error">Failed to load terms: {error.message}</div>;
  }

  const terms = itemsOf<PoliticianTerm>(data ?? null);

  if (!terms.length) {
    return (
      <div className="pol-tab__empty">
        <strong>No recorded terms yet.</strong>
        <p>We start tracking term boundaries from the moment this politician
           enters the dataset. Changes will appear here going forward.</p>
      </div>
    );
  }

  // Sort most-recent first; current term (no ended_at) at the top.
  const sorted = [...terms].sort((a, b) => {
    if (!a.ended_at && b.ended_at) return -1;
    if (a.ended_at && !b.ended_at) return 1;
    return new Date(b.started_at).getTime() - new Date(a.started_at).getTime();
  });

  return (
    <div className="pol-tab">
      <ol className="pol-terms">
        {sorted.map(t => <TermRow key={t.id} term={t} />)}
      </ol>
    </div>
  );
}

function TermRow({ term: t }: { term: PoliticianTerm }) {
  const current = t.ended_at === null;
  const locationBits = [t.province_territory].filter(Boolean).join(" · ");
  return (
    <li className={`pol-term ${current ? "pol-term--current" : ""}`}>
      <div className="pol-term__years">
        <span className="pol-term__year-start">{yearOf(t.started_at)}</span>
        <span className="pol-term__year-sep">→</span>
        <span className="pol-term__year-end">{t.ended_at ? yearOf(t.ended_at) : "present"}</span>
      </div>
      <div className="pol-term__body">
        <div className="pol-term__title">
          {t.office ?? "Elected office"}
          {current && <span className="pol-term__badge">Current</span>}
        </div>
        <div className="pol-term__meta">
          {t.party && <span className="pol-term__party">{t.party}</span>}
          {t.level && <span className="pol-term__level">· {t.level}</span>}
          {locationBits && <span className="pol-term__location">· {locationBits}</span>}
        </div>
        <div className="pol-term__dates">
          {fmtDate(t.started_at)} — {fmtDate(t.ended_at)}
          {t.source && <span className="pol-term__source"> · source: {t.source}</span>}
        </div>
      </div>
    </li>
  );
}
