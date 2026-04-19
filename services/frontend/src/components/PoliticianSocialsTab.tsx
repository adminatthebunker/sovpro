import {
  itemsOf,
  usePoliticianSocials,
  type PoliticianCore,
  type PoliticianSocial,
} from "../hooks/usePolitician";
import { SocialIcon, platformLabel } from "./SocialIcon";

interface Props {
  politicianId: string;
  politician: PoliticianCore | null;
}

/** Derive socials from the raw JSONB on the politician record — used as
 *  a fallback when the Phase 5 `/api/v1/socials/politicians/:id` route
 *  hasn't been deployed yet. */
function fallbackFromJson(p: PoliticianCore | null): PoliticianSocial[] {
  if (!p?.social_urls) return [];
  const out: PoliticianSocial[] = [];
  for (const [platform, url] of Object.entries(p.social_urls)) {
    if (!url || typeof url !== "string") continue;
    out.push({
      id: `jsonb:${platform}`,
      politician_id: p.id,
      platform,
      handle: null,
      url,
      last_verified_at: null,
      is_live: null,
      follower_count: null,
    });
  }
  return out;
}

export function PoliticianSocialsTab({ politicianId, politician }: Props) {
  const { data, loading, error, notFound } = usePoliticianSocials(politicianId);

  if (loading) return <div className="pol-tab__loading">Loading social accounts…</div>;

  // If the normalization endpoint doesn't exist yet (Phase 5 pending), fall
  // back to the JSONB blob so the tab is useful even pre-migration.
  const fromApi = itemsOf<PoliticianSocial>(data ?? null);
  const socials = fromApi.length ? fromApi : fallbackFromJson(politician);

  if (error && !socials.length) {
    return <div className="pol-tab__error">Failed to load socials: {error.message}</div>;
  }

  if (!socials.length) {
    return (
      <div className="pol-tab__empty">
        <strong>No social accounts on file.</strong>
        <p>{notFound
          ? "Social-handle tracking isn't available for this politician yet."
          : "Nothing has been discovered through Open North or the enrichment scrapers."}</p>
      </div>
    );
  }

  const usingFallback = fromApi.length === 0 && socials.length > 0;

  return (
    <div className="pol-tab">
      {usingFallback && (
        <p className="pol-tab__note">
          Showing raw handles from the Open North feed — liveness verification
          isn't wired up for this politician yet.
        </p>
      )}
      <div className="pol-socials__grid">
        {socials.map(s => <SocialCard key={s.id} social={s} />)}
      </div>
    </div>
  );
}

function SocialCard({ social: s }: { social: PoliticianSocial }) {
  const label = platformLabel(s.platform);
  const handle = s.handle ?? deriveHandle(s.url, s.platform);
  const neverVerified = s.last_verified_at === null;
  const status = neverVerified ? "unverified" : s.is_live ? "live" : "dead";

  return (
    <a
      className={`pol-social-card pol-social-card--${status}`}
      href={s.url}
      target="_blank"
      rel="noopener noreferrer"
    >
      <div className="pol-social-card__icon" aria-hidden="true">
        <SocialIcon platform={s.platform} size={20} />
      </div>
      <div className="pol-social-card__body">
        <div className="pol-social-card__platform">{label}</div>
        {handle && <div className="pol-social-card__handle">{handle}</div>}
        <div className="pol-social-card__url">{shortUrl(s.url)}</div>
      </div>
      <StatusBadge status={status} lastVerifiedAt={s.last_verified_at} />
    </a>
  );
}

function StatusBadge({
  status, lastVerifiedAt,
}: {
  status: "live" | "dead" | "unverified";
  lastVerifiedAt: string | null;
}) {
  if (status === "unverified") {
    return (
      <span className="pol-social-card__badge pol-social-card__badge--unverified" title="Never verified">
        ?
      </span>
    );
  }
  if (status === "dead") {
    return (
      <span
        className="pol-social-card__badge pol-social-card__badge--dead"
        title={lastVerifiedAt ? `Dead as of ${new Date(lastVerifiedAt).toLocaleDateString()}` : "Dead"}
      >
        dead
      </span>
    );
  }
  return (
    <span
      className="pol-social-card__badge pol-social-card__badge--live"
      title={lastVerifiedAt ? `Last verified ${new Date(lastVerifiedAt).toLocaleDateString()}` : "Live"}
    >
      live
    </span>
  );
}

function deriveHandle(url: string, platform: string): string | null {
  try {
    const u = new URL(url);
    const parts = u.pathname.split("/").filter(Boolean);
    if (!parts.length) return null;
    // twitter/x.com/foo, instagram.com/foo, tiktok.com/@foo
    const first = parts[0].replace(/^@/, "");
    return platform.toLowerCase() === "tiktok" ? `@${first}` : `@${first}`;
  } catch {
    return null;
  }
}

function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    return `${u.hostname}${u.pathname === "/" ? "" : u.pathname}`.replace(/\/$/, "");
  } catch {
    return url;
  }
}
