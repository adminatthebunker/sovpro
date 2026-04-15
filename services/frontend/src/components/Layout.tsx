import { Link, NavLink, Outlet } from "react-router-dom";
import { ShareMenu } from "./ShareMenu";

export function Layout() {
  return (
    <div className="shell">
      <header className="shell__header">
        <Link to="/" className="shell__brand">
          <span className="shell__logo" aria-hidden="true">🍁</span>
          <div>
            <h1>Canadian Political Data</h1>
            <p className="shell__tag">Where do Canadian politicians actually host their data?</p>
          </div>
        </Link>
        <nav className="shell__tabs" aria-label="Primary">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Home
          </NavLink>
          <NavLink to="/map" className={({ isActive }) => (isActive ? "active" : "")}>
            Map
          </NavLink>
          <NavLink to="/politicians" className={({ isActive }) => (isActive ? "active" : "")}>
            Politicians
          </NavLink>
          <ShareMenu />
          <a
            className="shell__contact"
            href="mailto:admin@thebunkerops.ca?subject=CanadianPoliticalData%20feedback"
            title="Send feedback by email"
          >
            ✉ Contact
          </a>
        </nav>
      </header>

      <Outlet />

      <footer className="shell__footer">
        <div className="shell__footer-row">
          <span>© {new Date().getFullYear()} Canadian Political Data</span>
          <span>· Built by <a href="https://bnkops.com/" target="_blank" rel="noopener noreferrer">The Bunker Operations</a></span>
          <span>· <a href="https://github.com/adminatthebunker/CanadianPoliticalData" target="_blank" rel="noopener noreferrer">Source on GitHub</a></span>
          <span>· <a href="mailto:admin@thebunkerops.ca?subject=CanadianPoliticalData%20feedback">Contact &amp; feedback</a></span>
        </div>
        <div className="shell__footer-row shell__footer-row--muted">
          <span>
            Open data from <a href="https://represent.opennorth.ca" target="_blank" rel="noopener noreferrer">Open North</a>
            {" and "}<a href="https://openparliament.ca" target="_blank" rel="noopener noreferrer">openparliament.ca</a>
            {" · Geolocation via MaxMind GeoLite2 · Released under the MIT license"}
          </span>
        </div>
      </footer>
    </div>
  );
}
