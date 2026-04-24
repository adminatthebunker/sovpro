import { useEffect, useRef, useState } from "react";

interface Props {
  model: string;
  onContinue: (rememberConsent: boolean) => void;
  onCancel: () => void;
}

/**
 * One-time (per-model) consent modal before we send a politician's
 * quotes to an external AI model via OpenRouter.
 *
 * Transparency is the whole point — the model identifier is rendered
 * verbatim so users consent to a specific third party, not "AI in
 * general." If an operator swaps OPENROUTER_MODEL, every user
 * re-consents on their next click (the localStorage record is keyed
 * on the model string).
 *
 * Structure mirrors PartyReportCard's `variant="modal"` pattern: an
 * outer div is the backdrop (onClick → cancel), inner div is the card
 * (stopPropagation), Escape key also cancels. No portal, no
 * focus-trap library — same minimal approach used elsewhere.
 */
export function AIConsentModal({ model, onContinue, onCancel }: Props) {
  const [remember, setRemember] = useState(true);
  const continueRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    continueRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div
      className="ai-consent-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ai-consent-heading"
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

        <h2 id="ai-consent-heading" className="ai-consent-modal__title">
          Analyze this politician's quotes with AI?
        </h2>

        <div className="ai-consent-modal__body">
          <p>
            The quotes currently shown in this card will be sent to an external
            AI model that looks for possible contradictions or shifts in
            position and returns short, model-generated rationales.
          </p>

          <div className="ai-consent-modal__model-row">
            <div className="ai-consent-modal__model-label">Model</div>
            <code className="ai-consent-modal__model-id">{model}</code>
            <div className="ai-consent-modal__model-sub">via OpenRouter</div>
          </div>

          <ul className="ai-consent-modal__disclosures">
            <li>
              Your selected quotes leave our servers and are sent to OpenRouter,
              which routes the request to the model host above.
            </li>
            <li>
              The model's output is a suggestion, not a verdict — read the
              source quotes before drawing conclusions.
            </li>
            <li>
              Free-tier models can be rate-limited or temporarily unavailable.
            </li>
          </ul>

          <label className="ai-consent-modal__remember">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            <span>Don't show this again for this browser (for this model).</span>
          </label>
        </div>

        <div className="ai-consent-modal__actions">
          <button
            type="button"
            className="ai-consent-modal__button ai-consent-modal__button--secondary"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            ref={continueRef}
            type="button"
            className="ai-consent-modal__button ai-consent-modal__button--primary"
            onClick={() => onContinue(remember)}
          >
            Continue and analyze
          </button>
        </div>
      </div>
    </div>
  );
}
