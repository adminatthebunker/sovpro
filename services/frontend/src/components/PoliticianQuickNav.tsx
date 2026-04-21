import type { PoliticianSearchGroup, PoliticianSort } from "../hooks/useSpeechSearch";

export interface PoliticianQuickNavProps {
  groups: PoliticianSearchGroup[];
  sort: PoliticianSort;
}

function formatMetric(g: PoliticianSearchGroup, sort: PoliticianSort): string {
  switch (sort) {
    case "mentions": {
      const n = g.mention_count;
      return `${n} ${n === 1 ? "quote" : "quotes"}`;
    }
    case "best_match":
      return g.best_similarity != null
        ? `${(g.best_similarity * 100).toFixed(0)}% match`
        : "—";
    case "avg_match":
      return g.avg_similarity != null
        ? `${(g.avg_similarity * 100).toFixed(0)}% avg`
        : "—";
    case "keyword_hits": {
      const n = g.keyword_hits;
      return `${n} keyword hit${n === 1 ? "" : "s"}`;
    }
  }
}

export function PoliticianQuickNav({ groups, sort }: PoliticianQuickNavProps) {
  if (groups.length === 0) return null;

  const onJump = (e: React.MouseEvent<HTMLAnchorElement>, id: string) => {
    // Prevent the hash from being written to the URL (keeps back-button
    // history clean — users expect Back to take them to a different
    // search, not to an earlier scroll position on the same search).
    e.preventDefault();
    const el = document.getElementById(`pg-card-${id}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      // Briefly highlight the landed-on card so the jump is visually
      // obvious on a long list. The CSS class self-expires.
      el.classList.add("politician-group--flash");
      window.setTimeout(() => el.classList.remove("politician-group--flash"), 1400);
    }
  };

  return (
    <nav className="politician-nav-grid" aria-label="Jump to politician">
      <ol className="politician-nav-grid__list">
        {groups.map((g) => {
          const { politician } = g;
          const metric = formatMetric(g, sort);
          return (
            <li key={politician.id} className="politician-nav-grid__item">
              <a
                className="politician-nav-grid__card"
                href={`#pg-card-${politician.id}`}
                onClick={(e) => onJump(e, politician.id)}
                title={`${politician.name ?? "Unknown"} — ${metric}`}
              >
                {politician.photo_url ? (
                  <img
                    src={politician.photo_url}
                    alt=""
                    className="politician-nav-grid__photo"
                    loading="lazy"
                    width={32}
                    height={32}
                  />
                ) : (
                  <div
                    className="politician-nav-grid__photo politician-nav-grid__photo--placeholder"
                    aria-hidden="true"
                  >
                    {(politician.name ?? "?").slice(0, 1)}
                  </div>
                )}
                <span className="politician-nav-grid__meta">
                  <span className="politician-nav-grid__name">{politician.name ?? "Unknown"}</span>
                  <span className="politician-nav-grid__metric">{metric}</span>
                </span>
              </a>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
