import { useCoverage, type CoverageJurisdiction } from "../hooks/useCoverage";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import "../styles/coverage.css";

const STATUS_LABEL: Record<CoverageJurisdiction["bills_status"], string> = {
  live: "Live",
  partial: "Partial",
  blocked: "Blocked",
  none: "Pending",
};

const STATUS_SYMBOL: Record<CoverageJurisdiction["bills_status"], string> = {
  live: "✓",
  partial: "◐",
  blocked: "⛔",
  none: "…",
};

function StatusPill({ status }: { status: CoverageJurisdiction["bills_status"] }) {
  return (
    <span className={`coverage__pill coverage__pill--${status}`}>
      <span className="coverage__pill-symbol" aria-hidden="true">{STATUS_SYMBOL[status]}</span>
      {STATUS_LABEL[status]}
    </span>
  );
}

function CountCell({ value, label }: { value: number; label: string }) {
  return (
    <div className="coverage__count">
      <span className="coverage__count-value">{value.toLocaleString()}</span>
      <span className="coverage__count-label">{label}</span>
    </div>
  );
}

export default function CoveragePage() {
  useDocumentTitle("Coverage");
  const { data, loading, error } = useCoverage();

  if (error) {
    return (
      <section className="coverage">
        <div className="coverage__error">Failed to load coverage: {error.message}</div>
      </section>
    );
  }

  if (loading || !data) {
    return (
      <section className="coverage">
        <header className="coverage__header">
          <h2 className="coverage__title">Coverage</h2>
          <p className="coverage__subtitle">Loading current state of every Canadian legislature we track…</p>
        </header>
      </section>
    );
  }

  const { jurisdictions, summary } = data;

  return (
    <section className="coverage">
      <header className="coverage__header">
        <h2 className="coverage__title">Coverage</h2>
        <p className="coverage__subtitle">
          Every Canadian legislature we track, with the current status of each data layer — bills,{" "}
          <abbr title="The official transcript of what was said in the legislature">Hansard</abbr>,{" "}
          votes, committees. Blocked jurisdictions are flagged with the specific reason.
        </p>
        <div className="coverage__summary" role="group" aria-label="Coverage summary">
          <CountCell value={summary.live} label="live" />
          <CountCell value={summary.partial} label="partial" />
          <CountCell value={summary.blocked} label="blocked" />
          <CountCell value={summary.none} label="pending" />
          <CountCell value={summary.total} label="total" />
        </div>
      </header>

      <div className="coverage__table-wrap">
        <table className="coverage__table">
          <thead>
            <tr>
              <th scope="col">Jurisdiction</th>
              <th scope="col">Seats</th>
              <th scope="col">
                <abbr title="Draft laws introduced in this legislature">Bills</abbr>
              </th>
              <th scope="col">
                <abbr title="The official transcript of what was said in the legislature">Hansard</abbr>
              </th>
              <th scope="col">
                <abbr title="How members voted on bills and motions">Votes</abbr>
              </th>
              <th scope="col">
                <abbr title="Working groups of members that review bills and hold hearings">Committees</abbr>
              </th>
              <th scope="col">Notes</th>
            </tr>
          </thead>
          <tbody>
            {jurisdictions.map((j) => (
              <tr key={j.jurisdiction}>
                <th scope="row" className="coverage__jurisdiction">
                  <div className="coverage__jurisdiction-inner">
                    <span className="coverage__code">{j.jurisdiction}</span>
                    <span className="coverage__legname">{j.legislature_name}</span>
                  </div>
                </th>
                <td className="coverage__seats">{j.seats ?? "—"}</td>
                <td><StatusPill status={j.bills_status} /></td>
                <td><StatusPill status={j.hansard_status} /></td>
                <td><StatusPill status={j.votes_status} /></td>
                <td><StatusPill status={j.committees_status} /></td>
                <td className="coverage__notes">
                  {j.blockers && <div className="coverage__blocker">{j.blockers}</div>}
                  {j.notes}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <footer className="coverage__footer">
        <p>Counts refresh hourly.</p>
        <p>
          See <a href="/blog">the blog</a> for updates as new jurisdictions come online.
        </p>
      </footer>
    </section>
  );
}
