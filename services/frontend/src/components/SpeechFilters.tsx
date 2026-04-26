import { useMemo } from "react";
import {
  SPEECH_TYPE_VALUES,
  type SpeechSearchFilter,
  type SpeechType,
} from "../hooks/useSpeechSearch";
import { useLegislativeSessions } from "../hooks/useLegislativeSessions";

// Canadian province/territory codes. Upper-case two-letter, per
// speech_chunks.province_territory.
const PROVINCES: Array<{ code: string; label: string }> = [
  { code: "AB", label: "Alberta" },
  { code: "BC", label: "British Columbia" },
  { code: "MB", label: "Manitoba" },
  { code: "NB", label: "New Brunswick" },
  { code: "NL", label: "Newfoundland & Labrador" },
  { code: "NS", label: "Nova Scotia" },
  { code: "NT", label: "Northwest Territories" },
  { code: "NU", label: "Nunavut" },
  { code: "ON", label: "Ontario" },
  { code: "PE", label: "Prince Edward Island" },
  { code: "QC", label: "Quebec" },
  { code: "SK", label: "Saskatchewan" },
  { code: "YT", label: "Yukon" },
];

const SPEECH_TYPE_LABELS: Record<SpeechType, string> = {
  floor: "Floor debate",
  question_period: "Question Period",
  statement: "Member statements",
  committee: "Committee",
  point_of_order: "Points of order",
  group: "Group / chant",
};

const MIN_SIMILARITY_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 0,    label: "All matches" },
  { value: 0.5,  label: "≥ 50% (looser)" },
  { value: 0.6,  label: "≥ 60%" },
  { value: 0.7,  label: "≥ 70%" },
  { value: 0.8,  label: "≥ 80% (strictest)" },
];

export interface SpeechFiltersProps {
  value: SpeechSearchFilter;
  onChange: (patch: Partial<SpeechSearchFilter>) => void;
  /** Hide filters that don't make sense in a particular context (e.g.
   *  politician-pinned search hides the level/province/party pickers
   *  because they're determined by the politician). */
  hide?: Array<"lang" | "level" | "province" | "party" | "from" | "to" | "exclude_presiding">;
}

export function SpeechFilters({ value, onChange, hide = [] }: SpeechFiltersProps) {
  const shows = (k: NonNullable<SpeechFiltersProps["hide"]>[number]) => !hide.includes(k);

  // Cascading session list scoped to the current jurisdiction. Returns
  // [] until the user picks a level, which keeps the dropdown empty
  // (and disabled) rather than showing a confusing all-jurisdictions list.
  const { sessions, loading: sessionsLoading } = useLegislativeSessions(
    value.level,
    value.province_territory,
  );

  // Keep the disclosure auto-open whenever a shared URL lands with any
  // advanced filter set. `key` on <details> resets the open state if the
  // filters get cleared programmatically (e.g. "Reset" button).
  const advancedActive = useMemo(
    () =>
      (value.min_similarity != null && value.min_similarity > 0) ||
      (value.parliament_number != null && value.session_number != null) ||
      (value.speech_types != null && value.speech_types.length > 0),
    [value.min_similarity, value.parliament_number, value.session_number, value.speech_types],
  );

  const speechTypeSet = useMemo(
    () => new Set<SpeechType>(value.speech_types ?? []),
    [value.speech_types],
  );

  const toggleSpeechType = (t: SpeechType) => {
    const next = new Set(speechTypeSet);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    onChange({
      speech_types: next.size > 0 ? Array.from(next) : undefined,
      page: 1,
    });
  };

  return (
    <>
    <div className="speech-filters" role="group" aria-label="Search filters">
      {shows("lang") && (
        <label className="speech-filters__item">
          <span className="speech-filters__label">Language</span>
          <select
            value={value.lang ?? "any"}
            onChange={(e) =>
              onChange({ lang: e.target.value as SpeechSearchFilter["lang"], page: 1 })
            }
          >
            <option value="any">Any</option>
            <option value="en">English</option>
            <option value="fr">Français</option>
          </select>
        </label>
      )}

      {shows("level") && (
        <label className="speech-filters__item">
          <span className="speech-filters__label">Level</span>
          <select
            value={value.level ?? ""}
            onChange={(e) =>
              onChange({
                level: (e.target.value || undefined) as SpeechSearchFilter["level"],
                page: 1,
              })
            }
          >
            <option value="">Any</option>
            <option value="federal">Federal</option>
            <option value="provincial">Provincial</option>
            <option value="municipal">Municipal</option>
          </select>
        </label>
      )}

      {shows("province") && (
        <label className="speech-filters__item">
          <span className="speech-filters__label">Province</span>
          <select
            value={value.province_territory ?? ""}
            onChange={(e) => onChange({ province_territory: e.target.value || undefined, page: 1 })}
          >
            <option value="">Any</option>
            {PROVINCES.map((p) => (
              <option key={p.code} value={p.code}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      )}

      {shows("party") && (
        <label className="speech-filters__item">
          <span className="speech-filters__label">Party</span>
          <input
            type="text"
            placeholder="e.g. Liberal"
            value={value.party ?? ""}
            onChange={(e) => onChange({ party: e.target.value || undefined, page: 1 })}
          />
        </label>
      )}

      {shows("from") && (
        <label className="speech-filters__item">
          <span className="speech-filters__label">From</span>
          <input
            type="date"
            value={value.from ?? ""}
            onChange={(e) => onChange({ from: e.target.value || undefined, page: 1 })}
          />
        </label>
      )}

      {shows("to") && (
        <label className="speech-filters__item">
          <span className="speech-filters__label">To</span>
          <input
            type="date"
            value={value.to ?? ""}
            onChange={(e) => onChange({ to: e.target.value || undefined, page: 1 })}
          />
        </label>
      )}

      {shows("exclude_presiding") && (
        <label
          className="speech-filters__item speech-filters__item--checkbox"
          title="Hide procedural chair speech (Speaker, Chair, Président) so substantive turns surface."
        >
          <input
            type="checkbox"
            checked={value.exclude_presiding === true}
            onChange={(e) => onChange({ exclude_presiding: e.target.checked || undefined, page: 1 })}
          />
          <span className="speech-filters__label">Hide chair speech</span>
        </label>
      )}
    </div>

    <details
      className="speech-filters__advanced"
      open={advancedActive}
      // `key` forces React to remount when the active state flips so the
      // browser-driven open/close stays in sync with the controlled prop.
      key={advancedActive ? "open" : "closed"}
    >
      <summary className="speech-filters__advanced-summary">
        Advanced filters
        {advancedActive && (
          <span className="speech-filters__advanced-active" aria-hidden="true"> · active</span>
        )}
      </summary>
      <div
        className="speech-filters speech-filters--advanced"
        role="group"
        aria-label="Advanced search filters"
      >
        <label className="speech-filters__item">
          <span className="speech-filters__label">Min similarity</span>
          <select
            value={value.min_similarity ?? 0}
            onChange={(e) => {
              const n = Number(e.target.value);
              onChange({
                min_similarity: n > 0 ? n : undefined,
                page: 1,
              });
            }}
            title="Drop weaker semantic matches. Only applies when a search term is present."
          >
            {MIN_SIMILARITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        <label className="speech-filters__item">
          <span className="speech-filters__label">Parliament &amp; session</span>
          <select
            value={
              value.parliament_number != null && value.session_number != null
                ? `${value.parliament_number}-${value.session_number}`
                : ""
            }
            onChange={(e) => {
              const v = e.target.value;
              if (!v) {
                onChange({ parliament_number: undefined, session_number: undefined, page: 1 });
                return;
              }
              const [pStr, sStr] = v.split("-");
              const p = Number(pStr);
              const s = Number(sStr);
              if (Number.isInteger(p) && p > 0 && Number.isInteger(s) && s > 0) {
                onChange({ parliament_number: p, session_number: s, page: 1 });
              }
            }}
            disabled={!value.level || sessionsLoading || sessions.length === 0}
            title={
              !value.level
                ? "Pick a level (federal/provincial) first to filter by session."
                : "Narrow to one session of one parliament."
            }
          >
            <option value="">
              {!value.level
                ? "Pick a level first"
                : sessionsLoading
                ? "Loading…"
                : sessions.length === 0
                ? "No sessions"
                : "Any session"}
            </option>
            {sessions.map((s) => {
              const label = s.name
                ? `${s.parliament_number}-${s.session_number} · ${s.name}`
                : `${s.parliament_number}th Parl., Sess. ${s.session_number}${
                    s.start_date ? ` (${s.start_date.slice(0, 4)})` : ""
                  }`;
              return (
                <option
                  key={`${s.parliament_number}-${s.session_number}`}
                  value={`${s.parliament_number}-${s.session_number}`}
                >
                  {label}
                </option>
              );
            })}
          </select>
        </label>

        <fieldset className="speech-filters__item speech-filters__item--checkboxes">
          <legend className="speech-filters__label">Speech type</legend>
          <div className="speech-filters__checkbox-group">
            {SPEECH_TYPE_VALUES.map((t) => (
              <label key={t} className="speech-filters__checkbox">
                <input
                  type="checkbox"
                  checked={speechTypeSet.has(t)}
                  onChange={() => toggleSpeechType(t)}
                />
                <span>{SPEECH_TYPE_LABELS[t]}</span>
              </label>
            ))}
          </div>
        </fieldset>
      </div>
    </details>
    </>
  );
}
