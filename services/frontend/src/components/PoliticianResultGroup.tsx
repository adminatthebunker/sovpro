import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type {
  GroupedSearchChunk,
  PoliticianSearchGroup,
  SpeechSearchItem,
} from "../hooks/useSpeechSearch";
import { SpeechResultCard } from "./SpeechResultCard";

/** English ordinal suffix: 1st, 2nd, 3rd, 4th … 11th, 12th, 13th, 21st. */
function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1: return `${n}st`;
    case 2: return `${n}nd`;
    case 3: return `${n}rd`;
    default: return `${n}th`;
  }
}

/** Rebuild a full SpeechSearchItem from a grouped chunk by re-attaching the
 *  politician from the group header. This lets us reuse SpeechResultCard
 *  with hideSpeaker=true — the card's share-target builder still needs
 *  item.politician.name for the attribution, so it can't just be null. */
function rehydrate(
  chunk: GroupedSearchChunk,
  politician: PoliticianSearchGroup["politician"],
): SpeechSearchItem {
  return {
    chunk_id: chunk.chunk_id,
    speech_id: chunk.speech_id,
    chunk_index: chunk.chunk_index,
    text: chunk.text,
    snippet_html: chunk.snippet_html,
    similarity: chunk.similarity,
    spoken_at: chunk.spoken_at,
    language: chunk.language,
    level: chunk.level,
    province_territory: chunk.province_territory,
    party_at_time: chunk.party_at_time,
    politician,
    speech: chunk.speech,
  };
}

export interface PoliticianResultGroupProps {
  group: PoliticianSearchGroup;
  /** Optional slot rendered below the chunk list. Used by the grouped
   *  view on /search to mount the per-card AI contradictions section.
   *  Kept as a generic ReactNode so this component stays UI-pure and
   *  the caller decides which cards get the button (e.g. filter to
   *  cards spanning ≥2 parliaments later). */
  footer?: ReactNode;
}

export function PoliticianResultGroup({ group, footer }: PoliticianResultGroupProps) {
  const { politician, chunks, best_similarity, avg_similarity, mention_count, keyword_hits } = group;

  const parliaments = new Set<number>();
  for (const c of chunks) {
    if (c.speech.session) parliaments.add(c.speech.session.parliament_number);
  }
  const parliamentCount = parliaments.size;
  // Use the server's mention_count (all qualified chunks above the
  // similarity threshold) rather than chunks.length, which is capped at
  // per_group_limit=5 and would undercount prolific speakers.
  const displayCount = mention_count ?? chunks.length;

  // Parliament divider: show between chunks where the parliament_number
  // changes. Chunks come pre-sorted by spoken_at, so consecutive same-parl
  // chunks stay visually grouped; transitions get a horizontal marker that
  // makes cross-parliament statements easy to pick out.
  let lastParliament: number | null = null;

  return (
    <article
      className="politician-group"
      id={`pg-card-${politician.id}`}
      aria-labelledby={`pg-${politician.id}`}
    >
      <header className="politician-group__header">
        {politician.photo_url ? (
          <img
            src={politician.photo_url}
            alt=""
            className="politician-group__photo"
            loading="lazy"
            width={56}
            height={56}
          />
        ) : (
          <div
            className="politician-group__photo politician-group__photo--placeholder"
            aria-hidden="true"
          >
            {(politician.name ?? "?").slice(0, 1)}
          </div>
        )}
        <div className="politician-group__meta">
          <Link
            to={`/politicians/${politician.id}`}
            className="politician-group__name"
            id={`pg-${politician.id}`}
          >
            {politician.name ?? "Unknown"}
          </Link>
          <span className="politician-group__sub">
            {politician.party ?? "—"}
            {" · "}
            <span className="politician-group__count">
              {displayCount} {displayCount === 1 ? "quote" : "quotes"}
              {parliamentCount > 1 ? ` across ${parliamentCount} parliaments` : null}
            </span>
          </span>
          <span
            className="politician-group__secondary-metrics"
            aria-label="Match statistics"
          >
            {avg_similarity != null && (
              <span
                className="politician-group__secondary-metric"
                title="Average cosine similarity across qualifying chunks"
              >
                avg {(avg_similarity * 100).toFixed(0)}%
              </span>
            )}
            <span
              className="politician-group__secondary-metric"
              title="Chunks that also match the keyword query literally"
            >
              {keyword_hits ?? 0} keyword hit{(keyword_hits ?? 0) === 1 ? "" : "s"}
            </span>
          </span>
        </div>
        {best_similarity !== null && (
          <span
            className="politician-group__similarity"
            title="Strongest cosine similarity across this politician's matching chunks"
          >
            {(best_similarity * 100).toFixed(0)}% best
          </span>
        )}
      </header>

      <ol className="politician-group__chunks" aria-label={`Quotes by ${politician.name ?? "this politician"}`}>
        {chunks.map((chunk) => {
          const thisParl = chunk.speech.session?.parliament_number ?? null;
          const showDivider =
            lastParliament !== null && thisParl !== null && lastParliament !== thisParl;
          const dividerLabel = thisParl !== null ? `${ordinal(thisParl)} Parliament` : null;
          lastParliament = thisParl;
          return (
            <li key={chunk.chunk_id} className="politician-group__chunk">
              {showDivider && dividerLabel && (
                <div className="politician-group__parl-divider" aria-hidden="true">
                  <span>{dividerLabel}</span>
                </div>
              )}
              <SpeechResultCard item={rehydrate(chunk, politician)} hideSpeaker />
            </li>
          );
        })}
      </ol>
      {footer && <div className="politician-group__footer">{footer}</div>}
    </article>
  );
}
