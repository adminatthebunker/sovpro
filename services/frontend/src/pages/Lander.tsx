import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { MapView } from "../components/MapView";
import type { FilterState } from "../components/Filters";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

const REPO_URL = "https://github.com/adminatthebunker/CanadianPoliticalData";

// Keep in sync with the postal regex used elsewhere in the app.
const POSTAL_RE = /^[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d$/;

// Pins-only filter for the backdrop. No polygons or connection lines — this
// is a decorative layer, and heavy fetches would slow the lander.
const BACKDROP_FILTERS: FilterState = {
  layer: "politicians",
  includeNoData: false,
};

export default function Lander() {
  useDocumentTitle(null); // base title only on the lander
  const navigate = useNavigate();
  const [postal, setPostal] = useState("");
  const [postalError, setPostalError] = useState<string | null>(null);
  const [hansard, setHansard] = useState("");
  const [betaOpen, setBetaOpen] = useState(false);

  function submitPostal(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = postal.trim();
    if (!POSTAL_RE.test(trimmed)) {
      setPostalError("Enter a valid Canadian postal code (e.g. K1A 0A6)");
      return;
    }
    const canonical = trimmed.replace(/\s|-/g, "").toUpperCase();
    navigate(`/map?postal=${canonical}`);
  }

  function submitHansard(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = hansard.trim();
    if (!trimmed) return;
    navigate(`/search?q=${encodeURIComponent(trimmed)}`);
  }

  useEffect(() => {
    if (!betaOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setBetaOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [betaOpen]);

  return (
    <div className="lander">
      <div className="lander__backdrop" aria-hidden="true">
        <MapView filters={BACKDROP_FILTERS} compact height="100%" />
        <div className="lander__scrim" />
      </div>
      <div className="lander__glass">
        <button
          type="button"
          className="lander__beta-badge"
          onClick={() => setBetaOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={betaOpen}
        >
          In Beta
        </button>
        <span className="lander__logo" aria-hidden="true">🍁</span>
        <h1 className="lander__title">Canadian Political Data</h1>
        <p className="lander__tagline">
          Canada's premier source for political data — where their sites are hosted, where they're posting, and what they're saying on the record.
        </p>

        <form className="lander__find" onSubmit={submitPostal}>
          <label className="lander__find-label" htmlFor="lander-postal">
            <span aria-hidden="true">📍</span> Find your data
          </label>
          <div className="lander__find-row">
            <input
              id="lander-postal"
              type="text"
              placeholder="Postal code (K1A 0A6)"
              value={postal}
              onChange={e => { setPostal(e.target.value); setPostalError(null); }}
              aria-label="Canadian postal code"
              aria-invalid={postalError ? true : undefined}
              aria-describedby={postalError ? "lander-postal-error" : undefined}
              maxLength={7}
            />
            <button type="submit" className="lander__btn lander__btn--primary">
              Find →
            </button>
          </div>
          {postalError && (
            <div id="lander-postal-error" className="lander__find-error" role="alert">
              {postalError}
            </div>
          )}
          <p className="lander__find-hint">
            We'll look up your MP, MLA, and municipal councillors and show where their sites are hosted, what they are saying, and their socials.
          </p>
        </form>

        <form className="lander__find" onSubmit={submitHansard}>
          <label className="lander__find-label" htmlFor="lander-hansard">
            <span aria-hidden="true">🔎</span> Search{" "}
            <abbr title="The official transcript of what was said in Parliament">Hansard</abbr>
          </label>
          <div className="lander__find-row">
            <input
              id="lander-hansard"
              type="search"
              placeholder='Search speeches (e.g. "carbon pricing")'
              value={hansard}
              onChange={e => setHansard(e.target.value)}
              aria-label="Search Canadian parliamentary speeches"
            />
            <button type="submit" className="lander__btn lander__btn--primary">
              Search →
            </button>
          </div>
          <p className="lander__find-hint">
            Search what every federal politician has said on the record.
          </p>
        </form>

        <div className="lander__cta">
          <Link to="/map" className="lander__btn">
            Explore the full map →
          </Link>
          <Link to="/politicians" className="lander__btn">
            Browse politicians →
          </Link>
        </div>
      </div>

      {betaOpen && (
        <div
          className="lander__beta-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="lander-beta-heading"
          onClick={() => setBetaOpen(false)}
        >
          <div
            className="lander__beta-modal-card"
            onClick={e => e.stopPropagation()}
          >
            <button
              type="button"
              className="lander__beta-modal-close"
              onClick={() => setBetaOpen(false)}
              aria-label="Close"
            >
              ×
            </button>
            <h2 id="lander-beta-heading" className="lander__beta-modal-title">
              Canadian Political Data is in Beta
            </h2>
            <div className="lander__beta-modal-body">
              <p>
                Canadian Political Data is being actively developed as an open
                project. You can follow the development path at{" "}
                <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
                  the repo
                </a>.
              </p>
              <p>
                While we're in Beta, the site will occasionally be down, and
                features will rapidly develop.
              </p>
            </div>
            <div className="lander__beta-modal-actions">
              <a
                href={REPO_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="lander__btn lander__btn--primary"
              >
                View on GitHub →
              </a>
              <button
                type="button"
                className="lander__btn"
                onClick={() => setBetaOpen(false)}
              >
                Got it
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
