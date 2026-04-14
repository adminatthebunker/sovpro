import { useEffect, useRef, useState } from "react";

const SHARE_URL = "https://canadianpoliticaldata.ca";
const SHARE_TITLE = "Canadian Political Data";
const SHARE_TEXT = "Where do Canadian politicians actually host their data?";

interface Target {
  key: string;
  label: string;
  icon: string;
  href: (u: string, t: string, x: string) => string;
}

const TARGETS: Target[] = [
  { key: "x",        label: "X / Twitter", icon: "𝕏",
    href: (u, _t, x) => `https://twitter.com/intent/tweet?url=${encodeURIComponent(u)}&text=${encodeURIComponent(x)}` },
  { key: "facebook", label: "Facebook", icon: "f",
    href: (u) => `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(u)}` },
  { key: "linkedin", label: "LinkedIn", icon: "in",
    href: (u) => `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(u)}` },
  { key: "reddit",   label: "Reddit", icon: "r/",
    href: (u, t) => `https://www.reddit.com/submit?url=${encodeURIComponent(u)}&title=${encodeURIComponent(t)}` },
  { key: "bluesky",  label: "Bluesky", icon: "🦋",
    href: (u, _t, x) => `https://bsky.app/intent/compose?text=${encodeURIComponent(`${x} ${u}`)}` },
  { key: "mastodon", label: "Mastodon", icon: "🐘",
    href: (u, _t, x) => `https://toot.kytta.dev/?text=${encodeURIComponent(`${x} ${u}`)}` },
  { key: "whatsapp", label: "WhatsApp", icon: "💬",
    href: (u, _t, x) => `https://api.whatsapp.com/send?text=${encodeURIComponent(`${x} ${u}`)}` },
  { key: "email",    label: "Email", icon: "✉",
    href: (u, t, x) => `mailto:?subject=${encodeURIComponent(t)}&body=${encodeURIComponent(`${x}\n\n${u}`)}` },
];

export function ShareMenu() {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

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

  const handleNative = async () => {
    if (typeof navigator !== "undefined" && navigator.share) {
      try {
        await navigator.share({ url: SHARE_URL, title: SHARE_TITLE, text: SHARE_TEXT });
        setOpen(false);
        return;
      } catch { /* user cancelled — fall through to dropdown */ }
    }
    setOpen(o => !o);
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(SHARE_URL);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch { /* noop */ }
  };

  return (
    <div className="share" ref={ref}>
      <button
        type="button"
        className="share__button"
        onClick={handleNative}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span aria-hidden>🔗</span> Share
      </button>
      {open && (
        <div className="share__menu" role="menu">
          <div className="share__head">Share this site</div>
          {TARGETS.map(t => (
            <a
              key={t.key}
              role="menuitem"
              className="share__item"
              href={t.href(SHARE_URL, SHARE_TITLE, SHARE_TEXT)}
              target={t.key === "email" ? undefined : "_blank"}
              rel="noopener noreferrer"
              onClick={() => setOpen(false)}
            >
              <span className="share__icon" aria-hidden>{t.icon}</span>
              <span>{t.label}</span>
            </a>
          ))}
          <button type="button" className="share__item share__item--copy" onClick={handleCopy} role="menuitem">
            <span className="share__icon" aria-hidden>📋</span>
            <span>{copied ? "Copied!" : "Copy link"}</span>
          </button>
        </div>
      )}
    </div>
  );
}
