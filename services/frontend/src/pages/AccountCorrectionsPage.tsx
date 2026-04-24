import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { userFetch, type CorrectionSubmission } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

const STATUS_LABEL: Record<CorrectionSubmission["status"], string> = {
  pending: "Pending",
  triaged: "Triaged",
  applied: "✓ Applied",
  rejected: "Rejected",
  duplicate: "Duplicate",
  spam: "Spam",
};

export default function AccountCorrectionsPage() {
  useDocumentTitle("Your corrections · Canadian Political Data");
  const { user, loading: authLoading, disabled } = useUserAuth();
  const [items, setItems] = useState<CorrectionSubmission[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    (async () => {
      try {
        const res = await userFetch<{ corrections: CorrectionSubmission[] }>("/me/corrections");
        setItems(res.corrections);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Load failed.");
      } finally {
        setLoading(false);
      }
    })();
  }, [user]);

  if (authLoading) return <section className="cpd-auth"><p>Loading…</p></section>;
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
        <h2>Sign in to see your corrections</h2>
        <p><Link to="/login?from=/account/corrections">Sign in →</Link></p>
      </section>
    );
  }

  return (
    <section className="cpd-auth cpd-auth--account">
      <h2>Your corrections</h2>
      {loading && <p>Loading…</p>}
      {error && <p className="cpd-auth__error" role="alert">{error}</p>}
      {items && items.length === 0 && (
        <p className="cpd-auth__muted">
          You haven't submitted any corrections yet.{" "}
          <Link to="/corrections">Submit one →</Link>
        </p>
      )}
      {items && items.length > 0 && (
        <ul className="cpd-corrections">
          {items.map(c => (
            <li key={c.id} className={`cpd-correction cpd-correction--${c.status}`}>
              <div className="cpd-correction__head">
                <strong>{c.subject_type}</strong>
                <span className={`cpd-correction__status cpd-correction__status--${c.status}`}>
                  {STATUS_LABEL[c.status]}
                </span>
                {c.credits_earned && c.credits_earned > 0 ? (
                  <span
                    className="cpd-auth__reward-badge"
                    title="Credits granted for this accepted correction"
                  >
                    +{c.credits_earned} credits
                  </span>
                ) : null}
                <span className="cpd-correction__date">
                  {new Date(c.received_at).toLocaleDateString()}
                </span>
              </div>
              <p className="cpd-correction__issue">{c.issue}</p>
              {c.proposed_fix && (
                <p className="cpd-correction__fix">
                  <em>Proposed fix:</em> {c.proposed_fix}
                </p>
              )}
              {c.reviewer_notes && (
                <p className="cpd-correction__note">
                  <em>Reviewer:</em> {c.reviewer_notes}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="cpd-auth__hint">
        <Link to="/corrections">Submit another correction →</Link>
      </p>
    </section>
  );
}
