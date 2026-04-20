import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { userFetch } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

/**
 * Magic-link redemption. The API emits links of the form
 *   ${PUBLIC_SITE_URL}/auth/verify?token=<nonce>
 * so this page just reads the token from the URL, POSTs it to the API,
 * and either lands the user on ?from=... (or /account) or shows a
 * friendly error.
 *
 * We don't surface the token value anywhere — it's single-use, but
 * logging it in the browser console or URL bar beyond the initial nav
 * is unnecessary exposure.
 */
export default function VerifyPage() {
  useDocumentTitle("Signing in…");
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { refresh } = useUserAuth();
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const token = params.get("token");
  const fromRaw = params.get("from");
  const from = fromRaw && fromRaw.startsWith("/") ? fromRaw : "/account";

  useEffect(() => {
    if (!token) {
      setError("Missing sign-in token. The link may have been truncated.");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await userFetch<{ id: string; email: string }>("/auth/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        });
        if (cancelled) return;
        await refresh();
        if (cancelled) return;
        setDone(true);
        // Small delay so the success state is visible before navigating.
        setTimeout(() => navigate(from, { replace: true }), 300);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof Error) setError(e.message);
        else setError("Sign-in failed.");
      }
    })();
    return () => { cancelled = true; };
  }, [token, from, navigate, refresh]);

  if (error) {
    return (
      <section className="cpd-auth">
        <h2>Sign-in failed</h2>
        <p className="cpd-auth__error" role="alert">{error}</p>
        <p>
          <Link to="/login">Request a new sign-in link →</Link>
        </p>
      </section>
    );
  }

  return (
    <section className="cpd-auth">
      <h2>Signing you in…</h2>
      <p>{done ? "Signed in. Redirecting…" : "One moment."}</p>
    </section>
  );
}
