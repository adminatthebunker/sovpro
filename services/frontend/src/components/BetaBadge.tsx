import { useEffect, useRef, useState } from "react";

export function BetaBadge() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="beta-badge" ref={ref}>
      <button
        type="button"
        className="beta-badge__btn"
        aria-expanded={open}
        aria-controls="beta-badge-panel"
        onClick={() => setOpen(o => !o)}
      >
        In Beta
      </button>
      {open && (
        <div
          id="beta-badge-panel"
          className="beta-badge__panel"
          role="status"
        >
          This project is in active development. The site will periodically be
          down and new features are being tested. Thanks for your patience as
          we build it in public.
        </div>
      )}
    </div>
  );
}
