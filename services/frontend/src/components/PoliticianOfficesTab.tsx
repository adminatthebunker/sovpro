import {
  itemsOf,
  usePoliticianOffices,
  type PoliticianOffice,
} from "../hooks/usePolitician";

interface Props {
  politicianId: string;
  level?: string | null;
}

export function PoliticianOfficesTab({ politicianId, level }: Props) {
  const { data, loading, error, notFound } = usePoliticianOffices(politicianId);

  if (loading) return <div className="pol-tab__loading">Loading offices…</div>;

  // Phase 7a may not be merged yet — the endpoint 404s and we show a
  // friendly placeholder instead of a broken tab.
  if (notFound) {
    return (
      <div className="pol-tab__empty">
        <strong>Not yet available.</strong>
        <p>Constituency and campaign office records will appear here once
           the offices ingestion job (Phase 7a) completes its first run.</p>
      </div>
    );
  }

  if (error) {
    return <div className="pol-tab__error">Failed to load offices: {error.message}</div>;
  }

  const offices = itemsOf<PoliticianOffice>(data ?? null);

  if (!offices.length) {
    return (
      <div className="pol-tab__empty">
        <strong>No offices on file.</strong>
        <p>No constituency or campaign office addresses are tracked for this politician yet.</p>
      </div>
    );
  }

  return (
    <div className="pol-tab">
      <ul className="pol-offices__list">
        {offices.map(o => <OfficeCard key={o.id} office={o} level={level} />)}
      </ul>
    </div>
  );
}

// Open North's `type` field uses "legislature" as a catch-all for "seat of
// government" — that means different things at different levels of office.
function labelForKind(kind: string | null, level: string | null | undefined): string {
  if (!kind) return "Office";
  if (kind === "legislature") {
    if (level === "municipal")  return "City hall office";
    if (level === "provincial") return "Legislature office";
    if (level === "federal")    return "Parliamentary office";
    return "Main office";
  }
  if (kind === "constituency") return "Constituency office";
  if (kind === "campaign")     return "Campaign office";
  if (kind === "ministerial")  return "Ministerial office";
  if (kind === "office")       return "Office";
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

function OfficeCard({ office: o, level }: { office: PoliticianOffice; level: string | null | undefined }) {
  const hasCoords = o.lat !== null && o.lon !== null;
  const label = labelForKind(o.kind, level);
  const cityLine = [o.city, o.province_territory, o.postal_code].filter(Boolean).join(", ");

  return (
    <li className="pol-office">
      <header className="pol-office__head">
        <div className="pol-office__label">{label}</div>
      </header>
      {o.address && <div className="pol-office__addr">{o.address}</div>}
      {cityLine && <div className="pol-office__addr">{cityLine}</div>}
      <dl className="pol-office__fields">
        {o.phone && (
          <div>
            <dt>Phone</dt>
            <dd><a href={`tel:${o.phone.replace(/[^\d+]/g, "")}`}>{o.phone}</a></dd>
          </div>
        )}
        {o.fax && (
          <div>
            <dt>Fax</dt>
            <dd>{o.fax}</dd>
          </div>
        )}
        {o.email && (
          <div>
            <dt>Email</dt>
            <dd><a href={`mailto:${o.email}`}>{o.email}</a></dd>
          </div>
        )}
        {o.hours && (
          <div>
            <dt>Hours</dt>
            <dd>{o.hours}</dd>
          </div>
        )}
      </dl>
      {hasCoords && (
        <div className="pol-office__map">
          <a
            href={`https://www.openstreetmap.org/?mlat=${o.lat}&mlon=${o.lon}#map=16/${o.lat}/${o.lon}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            View on map ({o.lat!.toFixed(4)}, {o.lon!.toFixed(4)})
          </a>
        </div>
      )}
    </li>
  );
}
