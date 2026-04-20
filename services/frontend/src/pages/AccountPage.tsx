import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { userFetch } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

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

  useEffect(() => {
    if (user) setDisplayName(user.display_name ?? "");
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

  return (
    <section className="cpd-auth cpd-auth--account">
      <h2>Your account</h2>
      <dl className="cpd-auth__meta">
        <dt>Email</dt><dd>{user.email}</dd>
        <dt>Account since</dt>
        <dd>{new Date(user.created_at).toLocaleDateString()}</dd>
        {user.last_login_at && (
          <>
            <dt>Last signed in</dt>
            <dd>{new Date(user.last_login_at).toLocaleString()}</dd>
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

      <div className="cpd-auth__actions">
        <Link to="/account/saved-searches" className="cpd-auth__linklike">
          Your saved searches →
        </Link>
        <Link to="/account/corrections" className="cpd-auth__linklike">
          Your corrections →
        </Link>
        <button type="button" onClick={onLogout} className="cpd-auth__linkbtn">
          Sign out
        </button>
      </div>
    </section>
  );
}
