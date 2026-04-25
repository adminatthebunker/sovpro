import type { ReportsMeta } from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useFullReportSubmit } from "../hooks/useFullReportSubmit";
import { FullReportConfirmModal } from "./FullReportConfirmModal";

interface Props {
  politicianId: string;
  query: string;
  meta: ReportsMeta | null;
}

/**
 * Standalone "Full report — analyze everything" peer button on each
 * politician card. Sits next to the free-tier AIContradictionAnalysis
 * as a discoverable entry point. The free-tier component also embeds
 * an inline upsell at the bottom of its result that triggers the same
 * flow via the same hook (see AIContradictionAnalysis).
 */
export function AIFullReportButton({ politicianId, query, meta }: Props) {
  const { user } = useUserAuth();
  const { estimating, estimate, submitting, error, openConfirm, submit, close } =
    useFullReportSubmit(politicianId, query);

  let disabledReason: string | null = null;
  if (!meta) disabledReason = "Loading premium reports status…";
  else if (!meta.enabled) disabledReason = "Premium reports not configured on this server.";
  else if (!user) disabledReason = "Sign in to generate a full report.";
  else if (!query.trim()) disabledReason = "Enter a search topic to generate a report.";

  return (
    <>
      <button
        type="button"
        className="ai-analysis__trigger ai-analysis__trigger--full"
        onClick={openConfirm}
        disabled={estimating || disabledReason !== null}
        title={disabledReason ?? undefined}
      >
        {estimating ? "Estimating…" : "Full report — analyze everything"}
      </button>

      {error && !estimate && (
        <p className="ai-analysis__disabled-hint" role="alert">
          {error}
        </p>
      )}

      {estimate && (
        <FullReportConfirmModal
          estimate={estimate}
          model={meta?.model ?? null}
          loading={submitting}
          error={error}
          onConfirm={submit}
          onCancel={close}
        />
      )}
    </>
  );
}
