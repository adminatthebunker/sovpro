import {
  itemsOf,
  usePoliticianChanges,
  type PoliticianChange,
} from "../hooks/usePolitician";

interface Props {
  politicianId: string;
}

const CHANGE_ICON: Record<string, string> = {
  party_switch:     "↔",
  office_change:    "⇄",
  retired:          "⏹",
  newly_elected:    "★",
  social_added:     "＋",
  social_removed:   "−",
  social_dead:      "☠",
  email_changed:    "✉",
  personal_url_changed: "🔗",
  photo_changed:    "🖼",
};

const CHANGE_LABEL: Record<string, string> = {
  party_switch:     "Party switch",
  office_change:    "Office change",
  retired:          "Retired",
  newly_elected:    "Newly elected",
  social_added:     "Social handle added",
  social_removed:   "Social handle removed",
  social_dead:      "Social handle went dead",
  email_changed:    "Email changed",
  personal_url_changed: "Personal URL changed",
  photo_changed:    "Photo changed",
};

function prettyType(t: string): string {
  return CHANGE_LABEL[t] ?? t.replace(/_/g, " ");
}

/** Render a change value (could be a string, null, or a structured object
 *  like {url, handle, platform} for social changes) as a readable string. */
function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (typeof v === "object") {
    const obj = v as Record<string, unknown>;
    // Social change payload: prefer platform + handle, then fall back to URL.
    if (obj.platform && obj.handle) return `@${obj.handle} on ${obj.platform}`;
    if (obj.url && typeof obj.url === "string") return obj.url;
    if (obj.handle) return String(obj.handle);
    // Last resort — dump as compact JSON.
    try { return JSON.stringify(obj); } catch { return String(v); }
  }
  return String(v);
}

export function PoliticianChangesTab({ politicianId }: Props) {
  const { data, loading, error, notFound } = usePoliticianChanges(politicianId);

  if (loading) return <div className="pol-tab__loading">Loading changes…</div>;
  if (notFound) {
    return (
      <div className="pol-tab__empty">
        <strong>Not yet available.</strong>
        <p>Politician-level change tracking (Phase 6) will populate this tab
           with party switches, office changes, and retirements as they're
           detected.</p>
      </div>
    );
  }
  if (error) {
    return <div className="pol-tab__error">Failed to load changes: {error.message}</div>;
  }

  const changes = itemsOf<PoliticianChange>(data ?? null);

  if (!changes.length) {
    return (
      <div className="pol-tab__empty">
        <strong>No changes recorded yet.</strong>
        <p>This politician's party, office, and social-handle record has been
           stable since we started tracking them.</p>
      </div>
    );
  }

  const sorted = [...changes].sort(
    (a, b) => new Date(b.detected_at).getTime() - new Date(a.detected_at).getTime()
  );

  return (
    <div className="pol-tab">
      <ol className="pol-changes">
        {sorted.map(c => <ChangeEntry key={c.id} change={c} />)}
      </ol>
    </div>
  );
}

function ChangeEntry({ change: c }: { change: PoliticianChange }) {
  const icon = CHANGE_ICON[c.change_type] ?? "•";
  const label = prettyType(c.change_type);

  return (
    <li className={`pol-change pol-change--${c.change_type}`}>
      <div className="pol-change__icon" aria-hidden="true">{icon}</div>
      <div className="pol-change__body">
        <div className="pol-change__head">
          <span className="pol-change__type">{label}</span>
          <time className="pol-change__time" dateTime={c.detected_at}>
            {new Date(c.detected_at).toLocaleString()}
          </time>
        </div>
        {c.summary && <p className="pol-change__summary">{c.summary}</p>}
        {(c.old_value != null || c.new_value != null) && (
          <div className="pol-change__diff">
            <del>{renderValue(c.old_value)}</del>
            <span> → </span>
            <ins>{renderValue(c.new_value)}</ins>
          </div>
        )}
      </div>
    </li>
  );
}
