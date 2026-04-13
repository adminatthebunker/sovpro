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

interface Props {
  active?: string;
  onChange: (party: string | undefined) => void;
}

export function PartyFilter({ active, onChange }: Props) {
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
        <button
          key={p.key}
          className={`party-filter__pill ${active === p.key ? "is-active" : ""}`}
          style={{
            "--party-color": p.color,
          } as React.CSSProperties}
          onClick={() => onChange(active === p.key ? undefined : p.key)}
          title={p.key}
        >
          <span className="party-filter__dot" style={{ background: p.color }} />
          {p.label}
        </button>
      ))}
    </div>
  );
}
