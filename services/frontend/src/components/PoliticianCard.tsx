import { Link } from "react-router-dom";
import type { PoliticianListItem } from "../hooks/usePoliticians";
import { SocialIcon, platformLabel } from "./SocialIcon";

const LEVEL_LABEL: Record<string, string> = {
  federal: "Federal",
  provincial: "Provincial",
  municipal: "Municipal",
};

// Heroicons v2 solid paths (MIT). Inlined so the card stays dep-free.
function IconSocials() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="M4.5 6.375a4.125 4.125 0 1 1 8.25 0 4.125 4.125 0 0 1-8.25 0ZM14.25 8.625a3.375 3.375 0 1 1 6.75 0 3.375 3.375 0 0 1-6.75 0ZM1.5 19.125a7.125 7.125 0 0 1 14.25 0v.003l-.001.119a.75.75 0 0 1-.363.63 13.067 13.067 0 0 1-6.761 1.873c-2.472 0-4.786-.684-6.76-1.873a.75.75 0 0 1-.364-.63l-.001-.122ZM17.25 19.128l-.001.144a2.25 2.25 0 0 1-.233.96 10.088 10.088 0 0 0 5.06-1.01.75.75 0 0 0 .42-.643 4.875 4.875 0 0 0-6.957-4.611 8.586 8.586 0 0 1 1.71 5.157v.003Z" />
    </svg>
  );
}
function IconOffices() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path fillRule="evenodd" d="M11.54 22.351l.07.04.028.016a.76.76 0 0 0 .723 0l.028-.015.071-.041a16.975 16.975 0 0 0 1.144-.742 19.58 19.58 0 0 0 2.683-2.282c1.944-1.99 3.963-4.98 3.963-8.827a8.25 8.25 0 0 0-16.5 0c0 3.846 2.02 6.837 3.963 8.827a19.58 19.58 0 0 0 2.682 2.282 16.975 16.975 0 0 0 1.145.742ZM12 13.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" clipRule="evenodd" />
    </svg>
  );
}
function IconTerms() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path fillRule="evenodd" d="M6.75 2.25A.75.75 0 0 1 7.5 3v1.5h9V3A.75.75 0 0 1 18 3v1.5h.75a3 3 0 0 1 3 3v11.25a3 3 0 0 1-3 3H5.25a3 3 0 0 1-3-3V7.5a3 3 0 0 1 3-3H6V3a.75.75 0 0 1 .75-.75Zm13.5 9a1.5 1.5 0 0 0-1.5-1.5H5.25a1.5 1.5 0 0 0-1.5 1.5v7.5a1.5 1.5 0 0 0 1.5 1.5h13.5a1.5 1.5 0 0 0 1.5-1.5v-7.5Z" clipRule="evenodd" />
    </svg>
  );
}
function IconChanges() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="m5.433 13.917 1.262-3.155A4 4 0 0 1 7.58 9.42l6.92-6.918a2.121 2.121 0 0 1 3 3l-6.92 6.918c-.383.383-.84.685-1.343.886l-3.154 1.262a.5.5 0 0 1-.65-.65Z" />
      <path d="M3.5 5.75c0-.69.56-1.25 1.25-1.25H10A.75.75 0 0 0 10 3H4.75A2.75 2.75 0 0 0 2 5.75v13.5A2.75 2.75 0 0 0 4.75 22h13.5A2.75 2.75 0 0 0 21 19.25V14a.75.75 0 0 0-1.5 0v5.25c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25V5.75Z" />
    </svg>
  );
}
function IconSpeeches() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="M8.25 4.5a3.75 3.75 0 1 1 7.5 0v8.25a3.75 3.75 0 1 1-7.5 0V4.5Z" />
      <path d="M6 10.5a.75.75 0 0 1 .75.75v1.5a5.25 5.25 0 1 0 10.5 0v-1.5a.75.75 0 0 1 1.5 0v1.5a6.751 6.751 0 0 1-6 6.709v2.291h3a.75.75 0 0 1 0 1.5h-7.5a.75.75 0 0 1 0-1.5h3v-2.291a6.751 6.751 0 0 1-6-6.709v-1.5A.75.75 0 0 1 6 10.5Z" />
    </svg>
  );
}

const TAB_JUMPS: Array<{ key: string; label: string; hint: string; Icon: () => JSX.Element }> = [
  { key: "socials",  label: "Socials",  hint: "Social handles + liveness", Icon: IconSocials },
  { key: "offices",  label: "Offices",  hint: "Constituency & Hill offices", Icon: IconOffices },
  { key: "terms",    label: "Terms",    hint: "Role / party history",      Icon: IconTerms },
  { key: "changes",  label: "Changes",  hint: "Infrastructure scan diffs",  Icon: IconChanges },
  { key: "speeches", label: "Speeches", hint: "Hansard / chamber speeches", Icon: IconSpeeches },
];

interface Props {
  politician: PoliticianListItem;
}

function formatQuoteDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function PoliticianCard({ politician }: Props) {
  const { id, name, party, elected_office, level, province_territory, constituency_name, photo_url, social_platforms, latest_speech_text, latest_speech_at } = politician;
  const platforms = social_platforms ?? [];
  const detailHref = `/politicians/${id}`;
  const quoteDate = formatQuoteDate(latest_speech_at);

  return (
    <div className="pol-card">
      <Link to={detailHref} className="pol-card__stretch" aria-label={`View ${name}`} />

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

      {latest_speech_text && (
        <Link
          to={`${detailHref}#speeches`}
          className="pol-card__quote"
          title={`Jump to speeches${quoteDate ? ` — most recent ${quoteDate}` : ""}`}
        >
          <span className="pol-card__quote-text">“{latest_speech_text}”</span>
          {quoteDate && <span className="pol-card__quote-date">{quoteDate}</span>}
        </Link>
      )}

      <nav className="pol-card__tab-nav" aria-label={`Jump to section for ${name}`}>
        {TAB_JUMPS.map(t => (
          <Link
            key={t.key}
            to={`${detailHref}#${t.key}`}
            className="pol-card__tab-btn"
            title={`${t.label} — ${t.hint}`}
            aria-label={`${t.label} — ${t.hint}`}
          >
            <t.Icon />
          </Link>
        ))}
      </nav>

      <div className="pol-card__socials">
        {platforms.length === 0 ? (
          <span className="pol-card__no-socials">No tracked socials</span>
        ) : (
          <ul className="pol-card__social-list">
            {platforms.map(p => (
              <li key={p} className="pol-card__social" title={platformLabel(p)}>
                <SocialIcon platform={p} size={16} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
