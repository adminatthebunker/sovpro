import type { ReactNode } from "react";

export interface BarListRow {
  key: string;
  label: ReactNode;
  value: number;
  /** Optional trailing meta (e.g. "72% match"). Rendered to the right of the bar. */
  meta?: string;
  /** Optional override color; defaults to --accent. */
  color?: string;
}

export interface BarListProps {
  rows: BarListRow[];
  /** Max count across the series for relative bar widths. If omitted, uses max(rows.value). */
  max?: number;
}

/** Horizontal bar list — one row per item, label on the left, filled
 *  bar showing relative value, numeric count on the right. Lightweight;
 *  uses div + percentage widths, no SVG. */
export function BarList({ rows, max }: BarListProps) {
  const ceiling = max ?? Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="barlist" role="list">
      {rows.map((row) => {
        const pct = Math.max(2, (row.value / ceiling) * 100); // min 2% so tiny values still show
        return (
          <li key={row.key} className="barlist__row">
            <div className="barlist__label">{row.label}</div>
            <div className="barlist__track">
              <div
                className="barlist__fill"
                style={{
                  width: `${pct}%`,
                  background: row.color ?? "var(--accent)",
                }}
              />
            </div>
            <div className="barlist__value">
              {row.value.toLocaleString()}
              {row.meta && <span className="barlist__meta"> · {row.meta}</span>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
