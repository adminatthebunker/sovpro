import { FormEvent, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { userFetch, UserAuthDisabledError } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

/**
 * Passwordless sign-in. User enters email → we POST /auth/request-link →
 * show a "check your email" confirmation. No passwords, no verification
 * codes to type — the magic link IS the verification. If SMTP is
 * unconfigured on the server, the API logs the would-be link to stdout
 * and still returns 202 (dev flow).
 */
export default function LoginPage() {
  useDocumentTitle("Sign in · Canadian Political Data");
  const { user } = useUserAuth();
  const [params] = useSearchParams();
  const from = params.get("from");
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (user) {
    return (
      <section className="cpd-auth">
        <h2>You're already signed in</h2>
        <p>Signed in as <strong>{user.email}</strong>.</p>
        <p>
          <Link to={from && from.startsWith("/") ? from : "/account"}>Continue →</Link>
        </p>
      </section>
    );
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await userFetch<{ ok: boolean }>("/auth/request-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim() }),
      });
      setSubmitted(true);
    } catch (e) {
      if (e instanceof UserAuthDisabledError) {
        setError("User accounts are disabled on this server (JWT_SECRET not set).");
      } else if (e instanceof Error) {
        setError(e.message);
      } else {
        setError("Sign-in request failed.");
      }
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return (
      <section className="cpd-auth">
        <h2>Check your email</h2>
        <p>
          We sent a one-time sign-in link to <strong>{email}</strong>.
          It expires in 15 minutes and can only be used once.
        </p>
        <p className="cpd-auth__muted">
          Didn't receive it? Check spam, or{" "}
          <button
            type="button"
            className="cpd-auth__linkbtn"
            onClick={() => setSubmitted(false)}
          >
            try a different address
          </button>
          .
        </p>
      </section>
    );
  }

  return (
    <section className="cpd-auth">
      <h2>Sign in</h2>
      <p className="cpd-auth__lead">
        We'll email you a one-time sign-in link. No password required.
      </p>
      <form className="cpd-auth__form" onSubmit={onSubmit}>
        <label>
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            autoFocus
            required
            autoComplete="email"
            placeholder="you@example.com"
          />
        </label>
        {error && <p className="cpd-auth__error" role="alert">{error}</p>}
        <button type="submit" disabled={loading || !email.trim()}>
          {loading ? "Sending…" : "Email me a sign-in link"}
        </button>
      </form>
      <p className="cpd-auth__hint">
        Accounts are optional. Signing in lets you save searches and get alerts when
        new speeches match.
      </p>
    </section>
  );
}
