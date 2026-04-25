import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { userFetch } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

interface CreditsSummary {
  balance: number;
  stripe_enabled: boolean;
}

function formatDate(iso: string, withTime = false): string {
  const opts: Intl.DateTimeFormatOptions = withTime
    ? { year: "numeric", month: "long", day: "numeric", hour: "numeric", minute: "2-digit" }
    : { year: "numeric", month: "long", day: "numeric" };
  return new Date(iso).toLocaleDateString(undefined, opts);
}

/**
 * Signed-in user's home. Profile (display_name), link to saved
 * searches, logout. If not signed in, prompt sign-in.
 */
export default function AccountPage() {
  useDocumentTitle("Your account · Canadian Political Data");
  const { user, loading, disabled, refresh, logout } = useUserAuth();
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [credits, setCredits] = useState<CreditsSummary | null>(null);

  useEffect(() => {
    if (user) setDisplayName(user.display_name ?? "");
  }, [user]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    userFetch<CreditsSummary>("/me/credits")
      .then((res) => {
        if (!cancelled) setCredits({ balance: res.balance, stripe_enabled: res.stripe_enabled });
      })
      .catch(() => {
        // Credits fetch failures don't block the rest of the page.
        // The balance chip just won't appear.
      });
    return () => {
      cancelled = true;
    };
  }, [user]);

  if (loading) {
    return <section className="cpd-auth"><p>Loading…</p></section>;
  }

  if (disabled) {
    return (
      <section className="cpd-auth">
        <h2>Accounts unavailable</h2>
        <p>User accounts are not configured on this server.</p>
      </section>
    );
  }

  if (!user) {
    return (
      <section className="cpd-auth">
        <h2>You're signed out</h2>
        <p>
          <Link to="/login?from=/account">Sign in →</Link>
        </p>
      </section>
    );
  }

  async function onSaveProfile(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSavedMessage(null);
    setError(null);
    try {
      await userFetch("/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: displayName.trim() || null }),
      });
      await refresh();
      setSavedMessage("Profile saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function onLogout() {
    await logout();
    navigate("/", { replace: true });
  }

  const displayLabel = user.display_name?.trim() || user.email.split("@")[0];

  return (
    <section className="cpd-auth cpd-auth--account">
      <header className="cpd-auth__header">
        <div className="cpd-auth__avatar" aria-hidden="true">
          {displayLabel?.charAt(0).toUpperCase() ?? "?"}
        </div>
        <div className="cpd-auth__header-body">
          <h2>{user.display_name?.trim() || "Your account"}</h2>
          <p className="cpd-auth__header-sub">{user.email}</p>
        </div>
      </header>

      <dl className="cpd-auth__meta">
        <dt>Member since</dt>
        <dd>{formatDate(user.created_at)}</dd>
        {user.last_login_at && (
          <>
            <dt>Last signed in</dt>
            <dd>{formatDate(user.last_login_at, true)}</dd>
          </>
        )}
      </dl>

      <form className="cpd-auth__form" onSubmit={onSaveProfile}>
        <label>
          <span>Display name (optional)</span>
          <input
            type="text"
            value={displayName}
            onChange={e => setDisplayName(e.target.value)}
            maxLength={100}
            placeholder="How you'd like to appear"
          />
        </label>
        {error && <p className="cpd-auth__error" role="alert">{error}</p>}
        {savedMessage && <p className="cpd-auth__ok" role="status">{savedMessage}</p>}
        <button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save profile"}
        </button>
      </form>

      <nav className="cpd-auth__tiles" aria-label="Account sections">
        <Link to="/account/saved-searches" className="cpd-auth__tile">
          <span className="cpd-auth__tile-title">Saved searches</span>
          <span className="cpd-auth__tile-sub">
            Queries you've bookmarked and alert preferences
          </span>
          <span className="cpd-auth__tile-arrow" aria-hidden="true">→</span>
        </Link>

        <Link to="/account/corrections" className="cpd-auth__tile">
          <span className="cpd-auth__tile-title">Corrections</span>
          <span className="cpd-auth__tile-sub">
            Data fixes you've submitted and their status
          </span>
          <span className="cpd-auth__tile-arrow" aria-hidden="true">→</span>
        </Link>

        {credits && credits.stripe_enabled && (
          <Link to="/account/credits" className="cpd-auth__tile cpd-auth__tile--credits">
            <span className="cpd-auth__tile-title">
              Credits
              <span className="cpd-auth__tile-chip">{credits.balance.toLocaleString()}</span>
            </span>
            <span className="cpd-auth__tile-sub">
              Balance, purchases, and ledger history
            </span>
            <span className="cpd-auth__tile-arrow" aria-hidden="true">→</span>
          </Link>
        )}

        <Link to="/account/reports" className="cpd-auth__tile">
          <span className="cpd-auth__tile-title">Reports</span>
          <span className="cpd-auth__tile-sub">
            Premium full-report syntheses you've generated
          </span>
          <span className="cpd-auth__tile-arrow" aria-hidden="true">→</span>
        </Link>
      </nav>

      <div className="cpd-auth__footer-row">
        <button type="button" onClick={onLogout} className="cpd-auth__signout">
          Sign out
        </button>
      </div>
    </section>
  );
}
