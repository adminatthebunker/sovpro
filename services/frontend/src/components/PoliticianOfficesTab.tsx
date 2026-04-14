import {
  itemsOf,
  usePoliticianOffices,
  type PoliticianOffice,
} from "../hooks/usePolitician";

interface Props {
  politicianId: string;
}

export function PoliticianOfficesTab({ politicianId }: Props) {
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
        {offices.map(o => <OfficeCard key={o.id} office={o} />)}
      </ul>
    </div>
  );
}

function OfficeCard({ office: o }: { office: PoliticianOffice }) {
  const hasCoords = o.latitude !== null && o.longitude !== null;
  const label = o.label ?? o.office_type ?? "Office";

  return (
    <li className="pol-office">
      <header className="pol-office__head">
        <div className="pol-office__label">{label}</div>
        {o.office_type && o.office_type !== label && (
          <span className="pol-office__type">{o.office_type}</span>
        )}
      </header>
      {o.address && (
        <div className="pol-office__addr">{o.address}</div>
      )}
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
            href={`https://www.openstreetmap.org/?mlat=${o.latitude}&mlon=${o.longitude}#map=16/${o.latitude}/${o.longitude}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            View on map ({o.latitude!.toFixed(4)}, {o.longitude!.toFixed(4)})
          </a>
        </div>
      )}
    </li>
  );
}
