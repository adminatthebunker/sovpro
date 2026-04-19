import { useEffect, useMemo, useRef } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useSpeech, type SpeechChunkSummary } from "../hooks/useSpeech";
import "../styles/speech-detail.css";

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleDateString("en-CA", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch {
    return iso.slice(0, 10);
  }
}

// Split the full speech text into chunk-anchored segments. The parent
// speech has a contiguous `text` field; `speech_chunks.char_start/end`
// are offsets into it. If offsets are stale (e.g. text was re-cleaned
// post-chunking), fall back to a single chunk rendered as-is.
interface Segment {
  chunk: SpeechChunkSummary | null;
  text: string;
}

function segmentsFromChunks(fullText: string, chunks: SpeechChunkSummary[]): Segment[] {
  if (chunks.length === 0) return [{ chunk: null, text: fullText }];
  const ordered = [...chunks].sort((a, b) => a.chunk_index - b.chunk_index);
  const out: Segment[] = [];
  let cursor = 0;
  for (const c of ordered) {
    if (c.char_start < cursor || c.char_end > fullText.length || c.char_end < c.char_start) {
      return [{ chunk: null, text: fullText }];
    }
    if (c.char_start > cursor) {
      out.push({ chunk: null, text: fullText.slice(cursor, c.char_start) });
    }
    out.push({ chunk: c, text: fullText.slice(c.char_start, c.char_end) });
    cursor = c.char_end;
  }
  if (cursor < fullText.length) {
    out.push({ chunk: null, text: fullText.slice(cursor) });
  }
  return out;
}

export default function SpeechDetailPage() {
  const { id } = useParams<{ id: string }>();
  const speechId = id ?? "";
  const { hash } = useLocation();
  const highlightChunkId = hash.startsWith("#chunk-") ? hash.slice("#chunk-".length) : null;
  const highlightRef = useRef<HTMLSpanElement | null>(null);

  const { data, loading, error, notFound } = useSpeech(speechId);

  useDocumentTitle(data?.speech ? `${data.speech.speaker_name_raw} — speech` : null);

  // Scroll the highlighted chunk into view once data arrives.
  useEffect(() => {
    if (!data || !highlightChunkId) return;
    const el = highlightRef.current;
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [data, highlightChunkId]);

  const segments = useMemo(
    () => (data ? segmentsFromChunks(data.speech.text, data.chunks) : []),
    [data],
  );

  if (loading) return <div className="speech-detail speech-detail--state">Loading speech…</div>;
  if (notFound) {
    return (
      <div className="speech-detail speech-detail--state">
        <Link to="/search" className="speech-detail__back">← Back to search</Link>
        <h1>Speech not found</h1>
        <p>No speech record with ID <code>{speechId}</code>.</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="speech-detail speech-detail--state">
        <Link to="/search" className="speech-detail__back">← Back to search</Link>
        <h1>Couldn't load speech</h1>
        <p>{error.message}</p>
      </div>
    );
  }
  if (!data) return null;

  const { speech } = data;
  const date = formatDate(speech.spoken_at);
  const hansardUrl = speech.source_anchor
    ? `${speech.source_url}#${speech.source_anchor}`
    : speech.source_url;

  return (
    <article className="speech-detail">
      <Link to="/search" className="speech-detail__back">← Back to search</Link>

      <header className="speech-detail__header">
        <div className="speech-detail__speaker">
          {speech.politician?.photo_url ? (
            <img
              src={speech.politician.photo_url}
              alt=""
              className="speech-detail__photo"
              width={64}
              height={64}
            />
          ) : (
            <div
              className="speech-detail__photo speech-detail__photo--placeholder"
              aria-hidden="true"
            >
              {speech.speaker_name_raw.slice(0, 1)}
            </div>
          )}
          <div className="speech-detail__speaker-meta">
            {speech.politician ? (
              <Link
                to={`/politicians/${speech.politician.id}`}
                className="speech-detail__speaker-name"
              >
                {speech.politician.name ?? speech.speaker_name_raw}
              </Link>
            ) : (
              <span className="speech-detail__speaker-name speech-detail__speaker-name--unresolved">
                {speech.speaker_name_raw}
              </span>
            )}
            <span className="speech-detail__speaker-sub">
              {speech.party_at_time ?? speech.politician?.party ?? "—"}
              {speech.constituency_at_time ? ` · ${speech.constituency_at_time}` : null}
            </span>
          </div>
        </div>

        <dl className="speech-detail__meta">
          {date && (
            <div>
              <dt>Date</dt>
              <dd>
                <time dateTime={speech.spoken_at ?? ""}>{date}</time>
              </dd>
            </div>
          )}
          {speech.session && (
            <div>
              <dt>Session</dt>
              <dd>
                {speech.session.parliament_number}th Parliament, Session {speech.session.session_number}
              </dd>
            </div>
          )}
          <div>
            <dt>Chamber</dt>
            <dd>
              {speech.level}
              {speech.province_territory ? ` · ${speech.province_territory}` : ""}
            </dd>
          </div>
          <div>
            <dt>Language</dt>
            <dd>{speech.language.toUpperCase()}</dd>
          </div>
        </dl>

        <a
          href={hansardUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="speech-detail__source"
        >
          View on source (Hansard) ↗
        </a>
      </header>

      <div className="speech-detail__body">
        {segments.map((seg, i) => {
          const isHighlight = seg.chunk && seg.chunk.id === highlightChunkId;
          return (
            <span
              key={i}
              id={seg.chunk ? `chunk-${seg.chunk.id}` : undefined}
              ref={isHighlight ? highlightRef : undefined}
              className={
                isHighlight
                  ? "speech-detail__segment speech-detail__segment--highlight"
                  : "speech-detail__segment"
              }
            >
              {seg.text}
            </span>
          );
        })}
      </div>
    </article>
  );
}
