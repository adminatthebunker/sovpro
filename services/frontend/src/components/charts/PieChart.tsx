import { useMemo } from "react";

export interface PieSegment {
  label: string;
  value: number;
  color: string;
  tooltip?: string;
}

export interface PieChartProps {
  segments: PieSegment[];
  size?: number;
  strokeWidth?: number;
}

// Converts polar to cartesian for SVG arc drawing. Angle in radians,
// 0 at 12 o'clock, clockwise positive — matches the typical pie.
function polar(cx: number, cy: number, r: number, angleRad: number): [number, number] {
  return [cx + r * Math.sin(angleRad), cy - r * Math.cos(angleRad)];
}

/** Pure-SVG pie chart (actually a donut — donuts are more readable at
 *  small sizes). No interactive library — uses native <title> for
 *  browser-provided tooltips on hover. */
export function PieChart({ segments, size = 160, strokeWidth = 28 }: PieChartProps) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - strokeWidth / 2;

  const paths = useMemo(() => {
    if (total === 0) return [];
    let cumulative = 0;
    return segments.map((seg) => {
      const startAngle = (cumulative / total) * Math.PI * 2;
      cumulative += seg.value;
      const endAngle = (cumulative / total) * Math.PI * 2;
      const [x0, y0] = polar(cx, cy, r, startAngle);
      const [x1, y1] = polar(cx, cy, r, endAngle);
      const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
      // Full-circle edge case (single segment): SVG arc collapses when
      // start == end. Use two semi-arcs instead.
      const pct = seg.value / total;
      const isFullCircle = pct >= 0.999;
      const d = isFullCircle
        ? `M ${cx} ${cy - r} A ${r} ${r} 0 1 1 ${cx - 0.01} ${cy - r} Z`
        : `M ${x0} ${y0} A ${r} ${r} 0 ${largeArc} 1 ${x1} ${y1}`;
      return { d, color: seg.color, label: seg.label, value: seg.value, tooltip: seg.tooltip ?? seg.label };
    });
  }, [segments, total, cx, cy, r]);

  if (total === 0) {
    return (
      <svg width={size} height={size} role="img" aria-label="Empty pie chart" className="pie pie--empty">
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border)" strokeWidth={strokeWidth} />
      </svg>
    );
  }

  return (
    <svg width={size} height={size} role="img" className="pie" viewBox={`0 0 ${size} ${size}`}>
      {paths.map((p, i) => (
        <path
          key={i}
          d={p.d}
          stroke={p.color}
          strokeWidth={strokeWidth}
          fill="none"
          className="pie__segment"
        >
          <title>{p.tooltip}</title>
        </path>
      ))}
    </svg>
  );
}
