import { Link, NavLink, Outlet } from "react-router-dom";
import { ShareMenu } from "./ShareMenu";
import { BetaBadge } from "./BetaBadge";
import { useUserAuth } from "../hooks/useUserAuth";

/**
 * Deterministic colour-from-string. 2 bytes of FNV-1a → HSL hue.
 * Same input always → same hue, so a user sees a consistent badge
 * across sessions without us persisting anything.
 */
function hueFromString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) % 360;
}

function initialFor(user: { display_name: string | null; email: string }): string {
  const src = (user.display_name?.trim() || user.email).trim();
  // First codepoint so names that start with emoji or non-ASCII still render.
  const cp = src.codePointAt(0);
  if (!cp) return "?";
  return String.fromCodePoint(cp).toUpperCase();
}

function AuthIndicator() {
  const { user, loading, disabled } = useUserAuth();
  // Don't flash "Sign in" while we're still fetching /me on first load.
  if (loading) return null;
  // Hide the indicator entirely if the server doesn't support accounts,
  // so we don't invite users into a broken flow.
  if (disabled) return null;

  if (user) {
    const hue = hueFromString(user.email.toLowerCase());
    const label = user.display_name?.trim() || user.email;
    return (
      <NavLink
        to="/account"
        title={label}
        aria-label={`Signed in as ${label}`}
        className={({ isActive }) => `shell__avatar${isActive ? " active" : ""}`}
        style={{
          background: `hsl(${hue} 55% 32%)`,
          borderColor: `hsl(${hue} 55% 45%)`,
        }}
      >
        <span aria-hidden="true">{initialFor(user)}</span>
      </NavLink>
    );
  }
  return (
    <NavLink to="/login" className={({ isActive }) => `shell__account shell__account--anon${isActive ? " active" : ""}`}>
      Sign in
    </NavLink>
  );
}

export function Layout() {
  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to main content</a>
      <header className="shell__header">
        <div className="shell__brand-group">
          <Link to="/" className="shell__brand">
            <span className="shell__logo" aria-hidden="true">🍁</span>
            <div>
              <h1>Canadian Political Data</h1>
              <p className="shell__tag">Canada's open source for political data.</p>
            </div>
          </Link>
          <BetaBadge />
        </div>
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
          <NavLink to="/search" className={({ isActive }) => (isActive ? "active" : "")}>
            Search
          </NavLink>
          <NavLink to="/coverage" className={({ isActive }) => (isActive ? "active" : "")}>
            Coverage
          </NavLink>
          <NavLink to="/blog" className={({ isActive }) => (isActive ? "active" : "")}>
            Blog
          </NavLink>
          <ShareMenu />
          <a
            className="shell__contact"
            href="mailto:admin@thebunkerops.ca?subject=CanadianPoliticalData%20feedback"
            title="Send feedback by email"
          >
            ✉ Contact
          </a>
          <AuthIndicator />
        </nav>
      </header>

      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>

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
