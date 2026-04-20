import { Link } from "react-router-dom";
import type { PoliticianCore } from "../hooks/usePolitician";
import { FollowPoliticianButton } from "./FollowPoliticianButton";

interface Props {
  politician: PoliticianCore;
}

const LEVEL_LABEL: Record<string, string> = {
  federal: "Federal",
  provincial: "Provincial/Territorial",
  municipal: "Municipal",
};

export function PoliticianDetailHeader({ politician: p }: Props) {
  const levelLabel = LEVEL_LABEL[p.level] ?? p.level;
  const locationBits = [p.constituency_name, p.province_territory].filter(Boolean).join(" · ");

  return (
    <header className="pol-detail__header">
      <Link className="pol-detail__back" to="/politicians">← Back to politicians</Link>

      <div className="pol-detail__head-body">
        {p.photo_url && (
          <img
            className="pol-detail__photo"
            src={p.photo_url}
            alt={`Photograph of ${p.name}`}
            loading="eager"
          />
        )}
        <div className="pol-detail__head-text">
          <h1 className="pol-detail__name">{p.name}</h1>
          {p.elected_office && (
            <div className="pol-detail__office">
              {p.elected_office}
              {p.party && <> · <span className="pol-detail__party">{p.party}</span></>}
            </div>
          )}
          <div className="pol-detail__meta">
            <span className="pol-detail__chip">{levelLabel}</span>
            {locationBits && <span className="pol-detail__chip pol-detail__chip--muted">{locationBits}</span>}
          </div>

          <div className="pol-detail__links">
            {p.personal_url && (
              <a href={p.personal_url} target="_blank" rel="noopener noreferrer">Personal site</a>
            )}
            {p.official_url && (
              <a href={p.official_url} target="_blank" rel="noopener noreferrer">Official page</a>
            )}
            {p.email && (
              <a href={`mailto:${p.email}`}>Email</a>
            )}
          </div>

          <div className="pol-detail__actions">
            <FollowPoliticianButton politicianId={p.id} politicianName={p.name} />
            <Link
              to={`/corrections?subject_type=politician&subject_id=${p.id}`}
              className="pol-detail__report"
              title="See a mistake in this politician's record? Let us know."
            >
              Report a correction
            </Link>
          </div>
        </div>
      </div>
    </header>
  );
}
