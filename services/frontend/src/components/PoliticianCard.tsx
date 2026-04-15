import { Link } from "react-router-dom";
import type { PoliticianListItem } from "../hooks/usePoliticians";

const LEVEL_LABEL: Record<string, string> = {
  federal: "Federal",
  provincial: "Provincial",
  municipal: "Municipal",
};

interface PlatformMeta {
  icon: string;
  color: string;
  label: string;
}

// Brand-adjacent colors, muted so cards stay readable at a glance.
const PLATFORM_META: Record<string, PlatformMeta> = {
  twitter:   { icon: "𝕏",  color: "#e7e9ea", label: "X / Twitter" },
  x:         { icon: "𝕏",  color: "#e7e9ea", label: "X" },
  facebook:  { icon: "f",  color: "#4267B2", label: "Facebook" },
  instagram: { icon: "◈",  color: "#E1306C", label: "Instagram" },
  linkedin:  { icon: "in", color: "#0A66C2", label: "LinkedIn" },
  youtube:   { icon: "▶",  color: "#FF0000", label: "YouTube" },
  tiktok:    { icon: "♪",  color: "#69C9D0", label: "TikTok" },
  threads:   { icon: "@",  color: "#cbd5e1", label: "Threads" },
  mastodon:  { icon: "M",  color: "#6364FF", label: "Mastodon" },
  bluesky:   { icon: "☁",  color: "#0285FF", label: "Bluesky" },
};

interface Props {
  politician: PoliticianListItem;
}

export function PoliticianCard({ politician }: Props) {
  const { id, name, party, elected_office, level, province_territory, constituency_name, photo_url, social_platforms } = politician;
  const platforms = social_platforms ?? [];

  return (
    <Link to={`/politicians/${id}`} className="pol-card">
      <div className="pol-card__top">
        <div className="pol-card__photo">
          {photo_url ? (
            <img src={photo_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
          ) : (
            <span className="pol-card__photo-fallback" aria-hidden="true">{name.slice(0, 1)}</span>
          )}
        </div>
        <div className="pol-card__body">
          <h3 className="pol-card__name">{name}</h3>
          <div className="pol-card__meta">
            {elected_office && <span>{elected_office}</span>}
            {party && <span className="pol-card__party"> · {party}</span>}
          </div>
          <div className="pol-card__riding">
            {constituency_name ?? LEVEL_LABEL[level]}
            {province_territory ? ` · ${province_territory}` : ""}
          </div>
        </div>
      </div>

      <div className="pol-card__socials">
        {platforms.length === 0 ? (
          <span className="pol-card__no-socials">No tracked socials</span>
        ) : (
          <ul className="pol-card__social-list">
            {platforms.map(p => {
              const meta = PLATFORM_META[p] ?? { icon: p.slice(0, 1).toUpperCase(), color: "#94a3b8", label: p };
              return (
                <li key={p} className="pol-card__social" title={meta.label}>
                  <span className="pol-card__social-icon" style={{ color: meta.color }}>{meta.icon}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Link>
  );
}
