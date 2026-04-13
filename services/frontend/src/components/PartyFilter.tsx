interface Party {
  key: string;
  label: string;
  color: string;
  level?: "federal" | "provincial";
}

// Federal parties for the main-map filter row. Provincial parties (UCP,
// Alberta NDP) live on the Alberta Referendum tab via the AB grade cards
// — they don't belong here because filtering the federal-MP map by a
// provincial party returns nothing.
const PARTIES: Party[] = [
  { key: "Liberal",        label: "Liberal",      color: "#e11d48", level: "federal" },
  { key: "Conservative",   label: "Conservative", color: "#1e3a8a", level: "federal" },
  { key: "NDP",            label: "NDP",          color: "#ea580c", level: "federal" },
  { key: "Bloc Québécois", label: "Bloc",         color: "#0891b2", level: "federal" },
  { key: "Green Party",    label: "Green",        color: "#16a34a", level: "federal" },
];

// Color lookup needs to know about provincial parties too so the AB tab's
// report card buttons can grab the right border color.
const ALL_PARTY_COLORS: Record<string, string> = {
  ...Object.fromEntries(PARTIES.map(p => [p.key, p.color])),
  "United Conservative Party":   "#1e40af",
  "Alberta New Democratic Party": "#f97316",
};

/** Resolve a party name → display color (for the report card border, etc.) */
export function partyColor(name: string): string {
  return ALL_PARTY_COLORS[name] ?? "#94a3b8";
}

interface Props {
  active?: string;
  /** Called when filter changes (clearing if user picks "All" or re-picks the same party) */
  onChange: (party: string | undefined) => void;
  /** Called whenever a party button is clicked. Combines with onChange so a single click both
   *  filters the map AND opens the report drawer. */
  onShowReport?: (party: string) => void;
}

/**
 * Single-button-per-party row. Clicking a party simultaneously filters the
 * map to that party and opens its report-card drawer. Each button is solid
 * party-color so the row reads as a color-coded shortcut bar.
 */
export function PartyFilter({ active, onChange, onShowReport }: Props) {
  function handleClick(p: Party) {
    if (active === p.key) {
      // Re-clicking the active party clears the filter (and any drawer)
      onChange(undefined);
    } else {
      onChange(p.key);
      onShowReport?.(p.key);
    }
  }

  return (
    <div className="party-filter">
      <span className="party-filter__label">Party:</span>
      <button
        className={`party-filter__pill party-filter__pill--all ${!active ? "is-active" : ""}`}
        onClick={() => onChange(undefined)}
        title="Clear party filter"
      >
        All
      </button>
      {PARTIES.map(p => (
        <button
          key={p.key}
          className={`party-filter__pill party-filter__pill--colored ${active === p.key ? "is-active" : ""}`}
          style={{
            "--party-color": p.color,
            background: p.color,
          } as React.CSSProperties}
          onClick={() => handleClick(p)}
          title={`Filter to ${p.key} and open report card`}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}
