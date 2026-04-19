import type { SpeechSearchFilter } from "../hooks/useSpeechSearch";

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

export interface SpeechFiltersProps {
  value: SpeechSearchFilter;
  onChange: (patch: Partial<SpeechSearchFilter>) => void;
  /** Hide filters that don't make sense in a particular context (e.g.
   *  politician-pinned search hides the level/province/party pickers
   *  because they're determined by the politician). */
  hide?: Array<"lang" | "level" | "province" | "party" | "from" | "to">;
}

export function SpeechFilters({ value, onChange, hide = [] }: SpeechFiltersProps) {
  const shows = (k: NonNullable<SpeechFiltersProps["hide"]>[number]) => !hide.includes(k);

  return (
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
    </div>
  );
}
