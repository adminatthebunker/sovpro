interface Party {
  key: string;
  label: string;
  color: string;
  level?: "federal" | "provincial";
}

const PARTIES: Party[] = [
  { key: "Liberal",                    label: "Liberal",      color: "#e11d48", level: "federal" },
  { key: "Conservative",               label: "Conservative", color: "#1e3a8a", level: "federal" },
  { key: "NDP",                        label: "NDP",          color: "#ea580c", level: "federal" },
  { key: "Bloc Québécois",             label: "Bloc",         color: "#0891b2", level: "federal" },
  { key: "Green Party",                label: "Green",        color: "#16a34a", level: "federal" },
  { key: "United Conservative Party",  label: "UCP",          color: "#1e40af", level: "provincial" },
  { key: "Alberta New Democratic Party", label: "AB NDP",     color: "#f97316", level: "provincial" },
];

/** Resolve a party name → display color (for the report card border, etc.) */
export function partyColor(name: string): string {
  return PARTIES.find(p => p.key === name)?.color ?? "#94a3b8";
}

interface Props {
  active?: string;
  onChange: (party: string | undefined) => void;
  onShowReport?: (party: string) => void;
}

export function PartyFilter({ active, onChange, onShowReport }: Props) {
  return (
    <div className="party-filter">
      <span className="party-filter__label">Party:</span>
      <button
        className={`party-filter__pill ${!active ? "is-active" : ""}`}
        onClick={() => onChange(undefined)}
      >
        All
      </button>
      {PARTIES.map(p => (
        <span key={p.key} className="party-filter__group">
          <button
            className={`party-filter__pill ${active === p.key ? "is-active" : ""}`}
            style={{ "--party-color": p.color } as React.CSSProperties}
            onClick={() => onChange(active === p.key ? undefined : p.key)}
            title={`Filter map to ${p.key}`}
          >
            <span className="party-filter__dot" style={{ background: p.color }} />
            {p.label}
          </button>
          {onShowReport && (
            <button
              className="party-filter__report"
              style={{ "--party-color": p.color } as React.CSSProperties}
              onClick={() => onShowReport(p.key)}
              title={`Open ${p.key} report card`}
              aria-label={`Open ${p.key} report card`}
            >
              📋
            </button>
          )}
        </span>
      ))}
    </div>
  );
}
