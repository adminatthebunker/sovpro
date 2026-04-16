import { FormEvent, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAdminAuth } from "../../hooks/useAdminAuth";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import "../../styles/admin.css";

export default function AdminLogin() {
  useDocumentTitle("Admin login");
  const { login, loading, error, isAuthed } = useAdminAuth();
  const [token, setToken] = useState("");
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const fromRaw = params.get("from");
  const from = fromRaw && fromRaw.startsWith("/admin") ? fromRaw : "/admin";

  if (isAuthed) {
    return (
      <section className="admin admin--login">
        <p>Already signed in.</p>
        <p><a href={from}>Continue to admin →</a></p>
      </section>
    );
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const ok = await login(token.trim());
    if (ok) navigate(from, { replace: true });
  }

  return (
    <section className="admin admin--login">
      <header className="admin__header">
        <div className="admin__brand">
          <span aria-hidden="true">⚙️</span>
          <h2>Admin login</h2>
        </div>
      </header>
      <form className="admin__login-form" onSubmit={onSubmit}>
        <label>
          <span>Admin token</span>
          <input
            type="password"
            value={token}
            onChange={e => setToken(e.target.value)}
            autoFocus
            spellCheck={false}
            autoComplete="off"
            placeholder="paste ADMIN_TOKEN"
          />
        </label>
        {error && <p className="admin__error" role="alert">{error}</p>}
        <button type="submit" disabled={loading || !token.trim()}>
          {loading ? "Checking…" : "Sign in"}
        </button>
        <p className="admin__hint">
          Set <code>ADMIN_TOKEN</code> in the server <code>.env</code> and restart the API
          to enable this panel. Generate with <code>openssl rand -hex 32</code>.
        </p>
      </form>
    </section>
  );
}
