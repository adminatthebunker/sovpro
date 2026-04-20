import { FormEvent, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { fetchJson, type CorrectionSubmission } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

const SUBJECT_OPTIONS: Array<{ value: CorrectionSubmission["subject_type"]; label: string }> = [
  { value: "general", label: "General feedback" },
  { value: "politician", label: "A politician's record" },
  { value: "speech", label: "A speech" },
  { value: "bill", label: "A bill" },
  { value: "vote", label: "A vote" },
  { value: "organization", label: "An organization" },
];

/**
 * Public corrections form at /corrections. Anyone can submit; signed-in
 * users get their `user_id` attached automatically (via the session
 * cookie — the API's optionalUser preHandler picks it up), so a
 * reviewer can see who to credit.
 *
 * URL prefill: `/corrections?subject_type=politician&subject_id=<uuid>`
 * is the pattern the "Report a correction" links use from context
 * pages, so users land on the form with the target already scoped.
 */
export default function CorrectionsPage() {
  useDocumentTitle("Submit a correction · Canadian Political Data");
  const { user } = useUserAuth();
  const [params] = useSearchParams();

  const prefillType = params.get("subject_type") as CorrectionSubmission["subject_type"] | null;
  const prefillId = params.get("subject_id");

  const [subjectType, setSubjectType] = useState<CorrectionSubmission["subject_type"]>(
    prefillType && SUBJECT_OPTIONS.some(o => o.value === prefillType) ? prefillType : "general"
  );
  const [subjectId, setSubjectId] = useState<string>(prefillId ?? "");
  const [issue, setIssue] = useState("");
  const [proposedFix, setProposedFix] = useState("");
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<CorrectionSubmission | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        subject_type: subjectType,
        issue: issue.trim(),
      };
      if (subjectId.trim()) body.subject_id = subjectId.trim();
      if (proposedFix.trim()) body.proposed_fix = proposedFix.trim();
      if (evidenceUrl.trim()) body.evidence_url = evidenceUrl.trim();
      if (name.trim()) body.submitter_name = name.trim();
      if (email.trim()) body.submitter_email = email.trim();

      const res = await fetchJson<CorrectionSubmission>("/corrections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setSubmitted(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submission failed.");
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <section className="cpd-auth">
        <h2>Thanks — correction received</h2>
        <p className="cpd-auth__muted">
          We'll review it and, if it's actionable, either update the record or reply
          with questions. Status is <strong>{submitted.status}</strong>.
        </p>
        {user && (
          <p>
            <Link to="/account/corrections">See all your submissions →</Link>
          </p>
        )}
        <p>
          <Link to="/corrections" onClick={() => setSubmitted(null)}>Submit another →</Link>
        </p>
      </section>
    );
  }

  return (
    <section className="cpd-auth cpd-auth--account">
      <h2>Submit a correction</h2>
      <p className="cpd-auth__lead">
        Spot an error or a missing detail? Let us know.
        {user
          ? <> We'll attach it to your account so we can credit you if it's applied.</>
          : <> Include an email so we can reach you if we need clarification.</>}
      </p>

      <form className="cpd-auth__form" onSubmit={onSubmit}>
        <label>
          <span>What are you correcting?</span>
          <select value={subjectType} onChange={e => setSubjectType(e.target.value as typeof subjectType)}>
            {SUBJECT_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>

        {subjectType !== "general" && (
          <label>
            <span>Subject ID (optional UUID, prefilled from link context)</span>
            <input
              type="text"
              value={subjectId}
              onChange={e => setSubjectId(e.target.value)}
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            />
          </label>
        )}

        <label>
          <span>The issue *</span>
          <textarea
            value={issue}
            onChange={e => setIssue(e.target.value)}
            rows={5}
            maxLength={5000}
            required
            placeholder="What's wrong or missing?"
          />
        </label>

        <label>
          <span>Proposed fix (optional)</span>
          <textarea
            value={proposedFix}
            onChange={e => setProposedFix(e.target.value)}
            rows={3}
            maxLength={5000}
            placeholder="If you have a specific fix in mind, share it."
          />
        </label>

        <label>
          <span>Evidence URL (optional)</span>
          <input
            type="url"
            value={evidenceUrl}
            onChange={e => setEvidenceUrl(e.target.value)}
            maxLength={2000}
            placeholder="https://…"
          />
        </label>

        {!user && (
          <>
            <label>
              <span>Your name (optional)</span>
              <input type="text" value={name} onChange={e => setName(e.target.value)} maxLength={200} />
            </label>
            <label>
              <span>Your email *</span>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                maxLength={320}
              />
            </label>
          </>
        )}

        {error && <p className="cpd-auth__error" role="alert">{error}</p>}
        <button type="submit" disabled={submitting || !issue.trim() || (!user && !email.trim())}>
          {submitting ? "Submitting…" : "Submit correction"}
        </button>
      </form>

      <p className="cpd-auth__hint">
        Corrections are reviewed manually. We don't publish submitter email addresses.
        {!user && (
          <> <Link to="/login?from=/corrections">Sign in</Link> if you want to track your submissions.</>
        )}
      </p>
    </section>
  );
}
