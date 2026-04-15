import { usePoliticianOpenparliament, type OpenparliamentEnvelope } from "../hooks/usePolitician";
import { PoliticianParliamentTimeline } from "./PoliticianParliamentTimeline";

interface Props {
  politicianId: string;
}

const OPENPARL_SITE = "https://openparliament.ca";

/** Renders a bilingual-field English value if present, then French, then ''. */
function bilingual(v: Record<string, string> | undefined): string {
  if (!v) return "";
  return v.en ?? v.fr ?? Object.values(v)[0] ?? "";
}

/** openparliament "related" links are API paths (e.g. "/politicians/foo/speeches/").
 *  Map to the public-site URL for a human-clickable link. */
function toSiteUrl(apiPath: string | undefined): string | null {
  if (!apiPath) return null;
  if (apiPath.startsWith("http")) return apiPath;
  return `${OPENPARL_SITE}${apiPath}`;
}

export function PoliticianOpenparliamentTab({ politicianId }: Props) {
  const { data, loading, error, notFound } = usePoliticianOpenparliament(politicianId);

  if (loading) return <div className="pol-detail__panel-loading">Loading from openparliament.ca…</div>;
  if (notFound) {
    // This shouldn't normally render — the tab is hidden when notFound.
    // Kept for defensive UX.
    return <div className="pol-detail__empty">No openparliament.ca data available for this politician.</div>;
  }
  if (error || !data) {
    return (
      <div className="pol-detail__empty">
        <h3>Couldn't reach openparliament.ca</h3>
        <p>{error?.message ?? "Unknown error"}</p>
      </div>
    );
  }

  const env: OpenparliamentEnvelope = data;
  const d = env.data;

  // Current membership = the one without an end_date (or the latest one).
  const current = (d.memberships ?? []).find(m => !m.end_date) ?? d.memberships?.[0];
  const party = bilingual(current?.party?.short_name);
  const riding = bilingual(current?.riding?.name);
  const province = current?.riding?.province;
  const termStart = current?.start_date;

  // Note: `data.related` returns API-only paths (e.g. /speeches/?politician=X)
  // that 405 in the browser. The only human-viewable aggregate is the
  // politician's own profile page, which embeds recent speeches/votes/bills.
  const profileUrl = d.url ? toSiteUrl(d.url) : null;
  const commonsLink = (d.links ?? []).find(l => /ourcommons\.ca/i.test(l.url))?.url;

  return (
    <div className="pol-parl">
      {env.warning && (
        <div className="pol-parl__warning" role="status">
          ⚠ {env.warning}
        </div>
      )}

      <div className="pol-parl__header">
        {d.image && (
          <img
            className="pol-parl__photo"
            src={d.image.startsWith("http") ? d.image : `${OPENPARL_SITE}${d.image}`}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
          />
        )}
        <div>
          <h3 className="pol-parl__name">{d.name ?? "Unknown"}</h3>
          {(party || riding || province) && (
            <p className="pol-parl__meta">
              {party && <span>{party}</span>}
              {riding && <span> · {riding}</span>}
              {province && <span> · {province}</span>}
            </p>
          )}
          {termStart && (
            <p className="pol-parl__meta pol-parl__meta--sub">
              Sitting since {new Date(termStart).toLocaleDateString("en-CA")}
            </p>
          )}
        </div>
      </div>

      <PoliticianParliamentTimeline politicianId={politicianId} />

      <div className="pol-parl__links">
        {profileUrl && (
          <a href={profileUrl} target="_blank" rel="noopener noreferrer" className="pol-parl__link--primary">
            View full record on openparliament.ca →
          </a>
        )}
        {commonsLink && <a href={commonsLink} target="_blank" rel="noopener noreferrer">House of Commons profile →</a>}
      </div>

      <footer className="pol-parl__attribution">
        Data from <a href={OPENPARL_SITE} target="_blank" rel="noopener noreferrer">openparliament.ca</a>
        {" · "}detail cached {env.source === "cache" ? "locally" : env.source === "fresh" ? "just now" : "stale"}
      </footer>
    </div>
  );
}
