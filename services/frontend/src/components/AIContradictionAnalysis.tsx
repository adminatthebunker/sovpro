import { useCallback, useMemo, useState } from "react";
import {
  userFetch,
  UserUnauthorizedError,
  UserAuthDisabledError,
} from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import type { GroupedSearchChunk } from "../hooks/useSpeechSearch";
import type { AIAnalyzeMeta } from "../hooks/useAIAnalyzeMeta";
import { AIConsentModal } from "./AIConsentModal";

interface AnalyzePair {
  a_chunk_id: string;
  b_chunk_id: string;
  kind: "contradiction" | "evolution" | "consistent";
  rationale: string;
}

interface AnalyzeResponse {
  model: string;
  analyzed_chunk_ids: string[];
  pairs: AnalyzePair[];
  summary: string | null;
}

interface Props {
  politicianId: string;
  politicianName: string;
  query: string;
  chunks: GroupedSearchChunk[];
  meta: AIAnalyzeMeta | null;
}

const CONSENT_KEY = "cpd_ai_analyze_consent_v1";

interface StoredConsent {
  model: string;
  consented_at: string;
}

function readConsent(): StoredConsent | null {
  try {
    const raw = localStorage.getItem(CONSENT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof (parsed as { model?: unknown }).model === "string"
    ) {
      return parsed as StoredConsent;
    }
    return null;
  } catch {
    return null;
  }
}

function writeConsent(model: string) {
  try {
    localStorage.setItem(
      CONSENT_KEY,
      JSON.stringify({ model, consented_at: new Date().toISOString() })
    );
  } catch {
    // Private mode / quota exceeded — user will just re-consent next
    // click. Not worth bubbling this error up.
  }
}

function clearConsent() {
  try {
    localStorage.removeItem(CONSENT_KEY);
  } catch {
    /* ignore */
  }
}

const KIND_LABEL: Record<AnalyzePair["kind"], string> = {
  contradiction: "Possible contradiction",
  evolution: "Position evolved",
  consistent: "Consistent",
};

/**
 * Per-card AI analysis section.
 *
 * Visible states (in order):
 *   1. meta disabled or unauth'd → greyed button + tooltip explaining why
 *   2. idle → primary button "Analyze for contradictions (AI)"
 *   3. consent modal open → button disabled; modal handles continue/cancel
 *   4. in-flight → loading spinner + cancel hint
 *   5. success → inline pair list (each with a kind badge + rationale)
 *   6. error → inline error strip + retry button
 *
 * Every response is framed as "the model suggests…" — we do not render
 * the word "contradiction" as a standalone verdict. The UI shows pairs
 * with their full source quotes attached so readers can judge the
 * suggestion rather than trust it.
 */
export function AIContradictionAnalysis({
  politicianId,
  politicianName,
  query,
  chunks,
  meta,
}: Props) {
  const { user } = useUserAuth();

  const [modalOpen, setModalOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);

  const chunkById = useMemo(() => {
    const m = new Map<string, GroupedSearchChunk>();
    for (const c of chunks) m.set(c.chunk_id, c);
    return m;
  }, [chunks]);

  const disabledReason = useMemo((): string | null => {
    if (!meta) return "Loading AI analysis status…";
    if (!meta.enabled) return "AI analysis is not configured on this server.";
    if (!user) return "Sign in to use AI analysis.";
    if (chunks.length < 2) return "Need at least 2 quotes to analyze.";
    return null;
  }, [meta, user, chunks.length]);

  const callAnalyze = useCallback(async () => {
    if (!query.trim()) {
      setError("A search query is required to analyze.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const chunkIds = chunks.slice(0, 10).map((c) => c.chunk_id);
      const response = await userFetch<AnalyzeResponse>("/contradictions/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          politician_id: politicianId,
          query,
          chunk_ids: chunkIds,
        }),
      });
      setResult(response);
    } catch (err) {
      if (err instanceof UserUnauthorizedError) {
        setError("Please sign in to use AI analysis.");
      } else if (err instanceof UserAuthDisabledError) {
        setError("User accounts are disabled on this server.");
      } else if (err instanceof Error && /^429\b/.test(err.message)) {
        setError("AI service is rate-limited. Try again in a moment.");
      } else if (err instanceof Error && /^503\b/.test(err.message)) {
        setError("AI analysis is not configured on this server.");
      } else if (err instanceof Error && /^504\b/.test(err.message)) {
        setError("AI service timed out. Try again in a moment.");
      } else if (err instanceof Error && /^502\b/.test(err.message)) {
        setError("AI model returned an unexpected response. Try again.");
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Unknown error");
      }
    } finally {
      setLoading(false);
    }
  }, [chunks, politicianId, query]);

  const handleClick = useCallback(() => {
    if (!meta?.model) return;
    const stored = readConsent();
    if (stored && stored.model === meta.model) {
      void callAnalyze();
      return;
    }
    setModalOpen(true);
  }, [meta, callAnalyze]);

  const handleContinue = useCallback(
    (remember: boolean) => {
      if (!meta?.model) return;
      if (remember) writeConsent(meta.model);
      setModalOpen(false);
      void callAnalyze();
    },
    [meta, callAnalyze]
  );

  const handleReviewSettings = useCallback(() => {
    clearConsent();
    setResult(null);
    setError(null);
  }, []);

  if (!meta) return null;

  return (
    <section
      className="politician-group__ai-analysis"
      aria-label={`AI analysis for ${politicianName}`}
    >
      <div className="ai-analysis__actions">
        <button
          type="button"
          className="ai-analysis__trigger"
          onClick={handleClick}
          disabled={loading || disabledReason !== null}
          title={disabledReason ?? undefined}
        >
          {loading ? "Analyzing…" : "Analyze for contradictions (AI)"}
        </button>
        {readConsent() && (
          <button
            type="button"
            className="ai-analysis__settings"
            onClick={handleReviewSettings}
            title="Clear consent and re-prompt on next click"
          >
            Review AI settings
          </button>
        )}
      </div>

      {disabledReason && !loading && (
        <p className="ai-analysis__disabled-hint">{disabledReason}</p>
      )}

      {error && (
        <div className="ai-analysis__error" role="alert">
          {error}
          <button
            type="button"
            className="ai-analysis__retry"
            onClick={() => void callAnalyze()}
          >
            Retry
          </button>
        </div>
      )}

      {result && (
        <div className="ai-analysis__result">
          <header className="ai-analysis__result-head">
            <span className="ai-analysis__result-label">The model suggests…</span>
            <code className="ai-analysis__result-model">{result.model}</code>
          </header>
          {result.summary && (
            <p className="ai-analysis__summary">{result.summary}</p>
          )}
          {result.pairs.length === 0 ? (
            <p className="ai-analysis__empty">
              The model did not identify any pairs among the shown quotes.
            </p>
          ) : (
            <ul className="ai-analysis__pairs">
              {result.pairs.map((pair, i) => {
                const a = chunkById.get(pair.a_chunk_id);
                const b = chunkById.get(pair.b_chunk_id);
                if (!a || !b) return null;
                return (
                  <li
                    key={`${pair.a_chunk_id}-${pair.b_chunk_id}-${i}`}
                    className={`ai-pair ai-pair--${pair.kind}`}
                  >
                    <div className="ai-pair__header">
                      <span
                        className={`ai-kind-badge ai-kind-badge--${pair.kind}`}
                      >
                        {KIND_LABEL[pair.kind]}
                      </span>
                    </div>
                    <div className="ai-pair__quotes">
                      <QuoteMini chunk={a} />
                      <QuoteMini chunk={b} />
                    </div>
                    <p className="ai-pair__rationale">{pair.rationale}</p>
                  </li>
                );
              })}
            </ul>
          )}
          <footer className="ai-analysis__foot">
            This is a model-generated suggestion, not a verdict. Read the
            source quotes before drawing conclusions.
          </footer>
        </div>
      )}

      {modalOpen && meta.model && (
        <AIConsentModal
          model={meta.model}
          onContinue={handleContinue}
          onCancel={() => setModalOpen(false)}
        />
      )}
    </section>
  );
}

function QuoteMini({ chunk }: { chunk: GroupedSearchChunk }) {
  const date = chunk.spoken_at ? chunk.spoken_at.slice(0, 10) : "unknown date";
  const parl = chunk.speech.session
    ? `${chunk.speech.session.parliament_number}-${chunk.speech.session.session_number}`
    : null;
  const truncated =
    chunk.text.length > 360 ? `${chunk.text.slice(0, 360)}…` : chunk.text;
  return (
    <blockquote className="ai-pair__quote">
      <div className="ai-pair__quote-meta">
        <span>{date}</span>
        {parl && (
          <span className="ai-pair__quote-parl">
            {" · "}Parliament {parl}
          </span>
        )}
        {chunk.party_at_time && (
          <span className="ai-pair__quote-party">
            {" · "}
            {chunk.party_at_time}
          </span>
        )}
      </div>
      <p className="ai-pair__quote-text">{truncated}</p>
    </blockquote>
  );
}
