import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  userFetch,
  UserUnauthorizedError,
  UserAuthDisabledError,
  type ReportEstimate,
} from "../api";

/**
 * Shared "estimate → confirm modal → submit" plumbing for the
 * premium-report flow. Two trigger surfaces consume this:
 *
 *   - AIFullReportButton            — standalone peer button on each card
 *   - AIContradictionAnalysis upsell — card at the bottom of the free
 *                                       contradictions output
 *
 * Both want identical behaviour (server-side estimate, modal with
 * cost/balance, submit, navigate to /account/reports?new=<id>) and
 * the same error-mapping for 402/429/409. Co-locating the logic here
 * keeps both surfaces in sync — adding e.g. a confirmation toast lands
 * in one place.
 */
export function useFullReportSubmit(politicianId: string, query: string) {
  const navigate = useNavigate();
  const [estimating, setEstimating] = useState(false);
  const [estimate, setEstimate] = useState<ReportEstimate | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const openConfirm = useCallback(async () => {
    if (!query.trim()) return;
    setEstimating(true);
    setError(null);
    setEstimate(null);
    try {
      const est = await userFetch<ReportEstimate>("/reports/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ politician_id: politicianId, query }),
      });
      setEstimate(est);
    } catch (e) {
      if (e instanceof UserUnauthorizedError) {
        setError("Please sign in to generate reports.");
      } else if (e instanceof UserAuthDisabledError) {
        setError("User accounts are disabled on this server.");
      } else if (e instanceof Error && /^503\b/.test(e.message)) {
        setError("Premium reports are not configured on this server.");
      } else if (e instanceof Error) {
        setError(e.message);
      } else {
        setError("Failed to estimate report cost.");
      }
    } finally {
      setEstimating(false);
    }
  }, [politicianId, query]);

  const submit = useCallback(async () => {
    if (!estimate) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await userFetch<{
        id: string;
        estimated_credits: number;
        balance_after: number;
      }>("/reports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ politician_id: politicianId, query }),
      });
      setEstimate(null);
      navigate(`/account/reports?new=${res.id}`);
    } catch (e) {
      if (e instanceof Error && /^402\b/.test(e.message)) {
        setError("Not enough credits. Buy credits and try again.");
      } else if (e instanceof Error && /^429\b/.test(e.message)) {
        setError("Daily report limit reached for your tier. Try again tomorrow.");
      } else if (e instanceof Error && /^409\b/.test(e.message)) {
        setError("Report cost has shifted. Re-open the dialog to see the updated cost.");
      } else if (e instanceof Error) {
        setError(e.message);
      } else {
        setError("Failed to submit report.");
      }
    } finally {
      setSubmitting(false);
    }
  }, [estimate, politicianId, query, navigate]);

  const close = useCallback(() => {
    setEstimate(null);
    setError(null);
  }, []);

  return { estimating, estimate, submitting, error, openConfirm, submit, close };
}
