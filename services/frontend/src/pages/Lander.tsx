import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { MapView } from "../components/MapView";
import type { FilterState } from "../components/Filters";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

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

  return (
    <div className="lander">
      <div className="lander__backdrop" aria-hidden="true">
        <MapView filters={BACKDROP_FILTERS} compact height="100%" />
        <div className="lander__scrim" />
      </div>
      <div className="lander__glass">
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
              maxLength={7}
            />
            <button type="submit" className="lander__btn lander__btn--primary">
              Find →
            </button>
          </div>
          {postalError && <div className="lander__find-error">{postalError}</div>}
          <p className="lander__find-hint">
            We'll look up your MP, MLA, and municipal councillors and show where their sites are hosted.
          </p>
        </form>

        <form className="lander__find" onSubmit={submitHansard}>
          <label className="lander__find-label" htmlFor="lander-hansard">
            <span aria-hidden="true">🔎</span> Search Hansard
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
    </div>
  );
}
