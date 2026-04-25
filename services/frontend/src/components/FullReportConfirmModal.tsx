import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import type { ReportEstimate } from "../api";

interface Props {
  estimate: ReportEstimate;
  model: string | null;
  loading: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Confirm-cost modal for the premium "Full report" flow.
 *
 * Distinct from AIConsentModal because this is a *purchase* flow:
 *   - Shows the credit cost (not just an opaque "send to AI?" prompt).
 *   - Branches on sufficient/insufficient balance with a "Buy credits"
 *     CTA that deep-links to /account/credits.
 *   - Includes the v1 disclaimer copy verbatim — the synthesis is
 *     generative; quotes are the ground truth.
 *
 * Reuses the same modal shell + ESC handler shape as AIConsentModal so
 * styling stays consistent (`.ai-consent-modal__*` classes).
 */
export function FullReportConfirmModal({
  estimate,
  model,
  loading,
  error,
  onConfirm,
  onCancel,
}: Props) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    confirmRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const sufficient = estimate.sufficient;

  return (
    <div
      className="ai-consent-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="full-report-confirm-heading"
      onClick={onCancel}
    >
      <div className="ai-consent-modal__card" onClick={(e) => e.stopPropagation()}>
        <button
          className="ai-consent-modal__close"
          onClick={onCancel}
          aria-label="Cancel"
          type="button"
        >
          ×
        </button>

        <h2 id="full-report-confirm-heading" className="ai-consent-modal__title">
          Generate full report?
        </h2>

        <div className="ai-consent-modal__body">
          <p>
            A model will read every quote we have from{" "}
            <strong>{estimate.politician.name ?? "this politician"}</strong> matching{" "}
            <em>"{estimate.query}"</em> and synthesise a report. Every claim links
            back to its source quote — <strong>always read the quotes</strong>{" "}
            before drawing conclusions. The synthesis is generative and can omit,
            misweight, or mischaracterise.
          </p>

          <div className="full-report-modal__cost">
            <div className="full-report-modal__cost-row">
              <span>Quotes analysed</span>
              <strong>
                {estimate.estimated_chunks}
                {estimate.capped && (
                  <span className="full-report-modal__capped"> (capped)</span>
                )}
              </strong>
            </div>
            <div className="full-report-modal__cost-row">
              <span>Cost</span>
              <strong>{estimate.estimated_credits} credits</strong>
            </div>
            <div className="full-report-modal__cost-row">
              <span>Your balance</span>
              <strong>{estimate.balance} credits</strong>
            </div>
            <div className="full-report-modal__cost-row">
              <span>After</span>
              <strong>
                {sufficient
                  ? `${estimate.balance - estimate.estimated_credits} credits`
                  : "—"}
              </strong>
            </div>
          </div>

          {model && (
            <div className="ai-consent-modal__model-row">
              <div className="ai-consent-modal__model-label">Model</div>
              <code className="ai-consent-modal__model-id">{model}</code>
              <div className="ai-consent-modal__model-sub">via OpenRouter</div>
            </div>
          )}

          {error && (
            <p className="full-report-modal__error" role="alert">
              {error}
            </p>
          )}

          <p className="full-report-modal__disclaimer">
            Canadian Political Data is not responsible for conclusions drawn from
            this brief.
          </p>
        </div>

        <div className="ai-consent-modal__footer">
          <button
            type="button"
            className="ai-consent-modal__cancel"
            onClick={onCancel}
            disabled={loading}
          >
            Cancel
          </button>
          {sufficient ? (
            <button
              ref={confirmRef}
              type="button"
              className="ai-consent-modal__continue"
              onClick={onConfirm}
              disabled={loading}
            >
              {loading
                ? "Submitting…"
                : `Generate report (–${estimate.estimated_credits} credits)`}
            </button>
          ) : (
            <Link
              to="/account/credits"
              className="ai-consent-modal__continue"
              onClick={onCancel}
            >
              Buy credits
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
