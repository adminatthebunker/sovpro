import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { SpeechSearchItem, SpeechSearchSocial } from "../hooks/useSpeechSearch";

const PLATFORM_ICON: Record<string, string> = {
  twitter: "𝕏",
  x: "𝕏",
  facebook: "f",
  instagram: "◎",
  tiktok: "♪",
  youtube: "▶",
  linkedin: "in",
  threads: "@",
  bluesky: "🦋",
  mastodon: "🐘",
};

const PLATFORM_LABEL: Record<string, string> = {
  twitter: "X / Twitter",
  x: "X / Twitter",
  facebook: "Facebook",
  instagram: "Instagram",
  tiktok: "TikTok",
  youtube: "YouTube",
  linkedin: "LinkedIn",
  threads: "Threads",
  bluesky: "Bluesky",
  mastodon: "Mastodon",
};

function platformIcon(p: string): string {
  return PLATFORM_ICON[p.toLowerCase()] ?? "●";
}

function platformLabel(p: string): string {
  return PLATFORM_LABEL[p.toLowerCase()] ?? p;
}

// ts_headline emits only <b>...</b> wrappers, but the source text from
// Postgres has never been HTML-escaped at ingest time. Allow <b> only,
// escape everything else. Without this, a speech transcript containing
// raw HTML would render in the DOM.
export function sanitizeHighlighted(html: string): { __html: string } {
  const BOLD_OPEN = "\u0000BOLD\u0000";
  const BOLD_CLOSE = "\u0000/BOLD\u0000";
  const s0 = html.replace(/<b>/gi, BOLD_OPEN).replace(/<\/b>/gi, BOLD_CLOSE);
  const escaped = s0.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const restored = escaped.split(BOLD_OPEN).join("<b>").split(BOLD_CLOSE).join("</b>");
  return { __html: restored };
}

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleDateString("en-CA", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso.slice(0, 10);
  }
}

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

function chamberLabel(level: string | null, prov: string | null): string | null {
  if (!level) return null;
  if (level === "federal") return "FED";
  if (level === "provincial" && prov) return prov.toUpperCase();
  if (level === "provincial") return "PROV";
  if (level === "municipal") return "MUNI";
  return level.toUpperCase();
}

export interface SpeechResultCardProps {
  item: SpeechSearchItem;
  /** Hide the photo + party badge when the card is rendered inside a
   *  politician's Speeches tab — the politician is already implied by
   *  the page context. */
  hideSpeaker?: boolean;
}

export function SpeechResultCard({ item, hideSpeaker = false }: SpeechResultCardProps) {
  const pol = item.politician;
  const date = formatDate(item.spoken_at);
  const chamber = chamberLabel(item.level, item.province_territory);
  const session = item.speech.session;
  const hansardUrl = item.speech.source_url
    ? item.speech.source_anchor
      ? `${item.speech.source_url}#${item.speech.source_anchor}`
      : item.speech.source_url
    : null;
  const internalUrl = `/speeches/${item.speech_id}#chunk-${item.chunk_id}`;

  return (
    <article className="speech-result">
      {!hideSpeaker && (
        <div className="speech-result__speaker">
          {pol?.photo_url ? (
            <img
              src={pol.photo_url}
              alt=""
              className="speech-result__photo"
              loading="lazy"
              width={44}
              height={44}
            />
          ) : (
            <div className="speech-result__photo speech-result__photo--placeholder" aria-hidden="true">
              {(pol?.name ?? item.speech.speaker_name_raw).slice(0, 1)}
            </div>
          )}
          <div className="speech-result__speaker-meta">
            {pol ? (
              <Link to={`/politicians/${pol.id}`} className="speech-result__speaker-name">
                {pol.name ?? item.speech.speaker_name_raw}
              </Link>
            ) : (
              <span className="speech-result__speaker-name speech-result__speaker-name--unresolved">
                {item.speech.speaker_name_raw}
              </span>
            )}
            <span className="speech-result__speaker-sub">
              {item.party_at_time ?? pol?.party ?? "—"}
              {chamber ? <> · <span className="speech-result__chamber">{chamber}</span></> : null}
            </span>
            {pol?.socials && pol.socials.length > 0 && (
              <SocialIcons socials={pol.socials} speakerName={pol.name ?? item.speech.speaker_name_raw} />
            )}
          </div>
        </div>
      )}

      <div className="speech-result__meta">
        {date && <time dateTime={item.spoken_at ?? ""}>{date}</time>}
        {session && (
          <span className="speech-result__session">
            {" · "}
            {ordinal(session.parliament_number)} Parl., Sess. {session.session_number}
          </span>
        )}
        <span className="speech-result__lang">{item.language.toUpperCase()}</span>
      </div>

      <p className="speech-result__snippet">
        {item.snippet_html ? (
          <span
            // safe: sanitizeHighlighted only re-admits <b> tags
            dangerouslySetInnerHTML={sanitizeHighlighted(item.snippet_html)}
          />
        ) : (
          item.text.slice(0, 280) + (item.text.length > 280 ? "…" : "")
        )}
      </p>

      <div className="speech-result__actions">
        <Link to={internalUrl} className="speech-result__action">
          View speech →
        </Link>
        {hansardUrl && (
          <a
            href={hansardUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="speech-result__action speech-result__action--secondary"
          >
            Hansard ↗
          </a>
        )}
        <QuoteShareButton item={item} internalUrl={internalUrl} />
        {item.similarity !== null && (
          <span className="speech-result__similarity" title="Cosine similarity to query">
            {(item.similarity * 100).toFixed(0)}% match
          </span>
        )}
      </div>
    </article>
  );
}

function SocialIcons({ socials, speakerName }: { socials: SpeechSearchSocial[]; speakerName: string }) {
  return (
    <span className="speech-result__socials" aria-label={`${speakerName} on social media`}>
      {socials.map((s) => (
        <a
          key={`${s.platform}:${s.url}`}
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
          className="speech-result__social"
          title={`${speakerName} on ${platformLabel(s.platform)}`}
          aria-label={`${speakerName} on ${platformLabel(s.platform)}`}
          onClick={(e) => e.stopPropagation()}
        >
          <span aria-hidden="true">{platformIcon(s.platform)}</span>
        </a>
      ))}
    </span>
  );
}

interface ShareTarget {
  key: string;
  label: string;
  icon: string;
  href: (url: string, text: string) => string;
  newTab: boolean;
}

const SHARE_TARGETS: ShareTarget[] = [
  { key: "x",        label: "X / Twitter", icon: "𝕏", newTab: true,
    href: (u, x) => `https://twitter.com/intent/tweet?url=${encodeURIComponent(u)}&text=${encodeURIComponent(x)}` },
  { key: "bluesky",  label: "Bluesky", icon: "🦋", newTab: true,
    href: (u, x) => `https://bsky.app/intent/compose?text=${encodeURIComponent(`${x} ${u}`)}` },
  { key: "facebook", label: "Facebook", icon: "f", newTab: true,
    href: (u) => `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(u)}` },
  { key: "linkedin", label: "LinkedIn", icon: "in", newTab: true,
    href: (u) => `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(u)}` },
  { key: "reddit",   label: "Reddit", icon: "r/", newTab: true,
    href: (u, x) => `https://www.reddit.com/submit?url=${encodeURIComponent(u)}&title=${encodeURIComponent(x)}` },
  { key: "whatsapp", label: "WhatsApp", icon: "💬", newTab: true,
    href: (u, x) => `https://api.whatsapp.com/send?text=${encodeURIComponent(`${x} ${u}`)}` },
  { key: "email",    label: "Email", icon: "✉", newTab: false,
    href: (u, x) => `mailto:?subject=${encodeURIComponent("Quote from Canadian Political Data")}&body=${encodeURIComponent(`${x}\n\n${u}`)}` },
];

function buildShareText(item: SpeechSearchItem): string {
  const speaker = item.politician?.name ?? item.speech.speaker_name_raw;
  const date = item.spoken_at ? new Date(item.spoken_at).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" }) : "";
  // Cap at 220 chars to leave room for attribution + URL inside Twitter's 280 budget.
  const quote = item.text.length > 220 ? `${item.text.slice(0, 217)}…` : item.text;
  const attribution = date ? `— ${speaker}, ${date}` : `— ${speaker}`;
  return `“${quote}” ${attribution}`;
}

function QuoteShareButton({ item, internalUrl }: { item: SpeechSearchItem; internalUrl: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<"link" | "quote" | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const absoluteUrl = typeof window !== "undefined"
    ? new URL(internalUrl, window.location.origin).toString()
    : internalUrl;
  const shareText = buildShareText(item);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const handleButton = async () => {
    if (typeof navigator !== "undefined" && navigator.share) {
      try {
        await navigator.share({ url: absoluteUrl, text: shareText, title: "Canadian Political Data" });
        return;
      } catch { /* user cancelled — fall through to dropdown */ }
    }
    setOpen((o) => !o);
  };

  const copy = async (what: "link" | "quote") => {
    try {
      await navigator.clipboard.writeText(what === "link" ? absoluteUrl : `${shareText}\n${absoluteUrl}`);
      setCopied(what);
      setTimeout(() => setCopied(null), 1600);
    } catch { /* noop */ }
  };

  return (
    <div className="speech-result__share" ref={ref}>
      <button
        type="button"
        className="speech-result__action speech-result__action--share"
        onClick={handleButton}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span aria-hidden="true">↗</span> Share
      </button>
      {open && (
        <div className="speech-result__share-menu" role="menu">
          <div className="speech-result__share-head">Share this quote</div>
          {SHARE_TARGETS.map((t) => (
            <a
              key={t.key}
              role="menuitem"
              className="speech-result__share-item"
              href={t.href(absoluteUrl, shareText)}
              target={t.newTab ? "_blank" : undefined}
              rel={t.newTab ? "noopener noreferrer" : undefined}
              onClick={() => setOpen(false)}
            >
              <span className="speech-result__share-icon" aria-hidden="true">{t.icon}</span>
              <span>{t.label}</span>
            </a>
          ))}
          <button
            type="button"
            role="menuitem"
            className="speech-result__share-item speech-result__share-item--copy"
            onClick={() => copy("quote")}
          >
            <span className="speech-result__share-icon" aria-hidden="true">📋</span>
            <span>{copied === "quote" ? "Copied quote!" : "Copy quote + link"}</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="speech-result__share-item speech-result__share-item--copy"
            onClick={() => copy("link")}
          >
            <span className="speech-result__share-icon" aria-hidden="true">🔗</span>
            <span>{copied === "link" ? "Copied!" : "Copy link"}</span>
          </button>
        </div>
      )}
    </div>
  );
}
