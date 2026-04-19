export interface YearHistogramBin {
  year: number;
  count: number;
}

export interface YearHistogramProps {
  bins: YearHistogramBin[];
  height?: number;
}

/** Vertical bar chart. Fills gaps in the year range (so 2012, 2015, 2016
 *  becomes 2012, 2013 (empty), 2014 (empty), 2015, 2016) — makes density
 *  shifts visually honest. Uses SVG for clean crisp rendering. */
export function YearHistogram({ bins, height = 120 }: YearHistogramProps) {
  if (bins.length === 0) {
    return <p className="year-hist__empty">No dated chunks in this set.</p>;
  }

  const years = bins.map((b) => b.year);
  const minYear = Math.min(...years);
  const maxYear = Math.max(...years);
  const countByYear = new Map(bins.map((b) => [b.year, b.count]));

  const filled: YearHistogramBin[] = [];
  for (let y = minYear; y <= maxYear; y++) {
    filled.push({ year: y, count: countByYear.get(y) ?? 0 });
  }
  const maxCount = Math.max(1, ...filled.map((b) => b.count));

  const barGap = 2;
  const totalBars = filled.length;
  // Use 100% viewBox width and let CSS scale; barW becomes proportional.
  const viewW = 100;
  const barW = (viewW - barGap * (totalBars - 1)) / totalBars;

  return (
    <svg
      className="year-hist"
      viewBox={`0 0 ${viewW} ${height}`}
      preserveAspectRatio="none"
      role="img"
      style={{ width: "100%", height }}
    >
      {filled.map((bin, i) => {
        const barH = bin.count === 0 ? 1 : (bin.count / maxCount) * (height - 14);
        const x = i * (barW + barGap);
        const y = height - barH - 14; // leave room for year labels
        return (
          <g key={bin.year}>
            <rect
              x={x}
              y={y}
              width={barW}
              height={barH}
              fill={bin.count === 0 ? "var(--border)" : "var(--accent)"}
              opacity={bin.count === 0 ? 0.3 : 0.85}
            >
              <title>{`${bin.year}: ${bin.count}`}</title>
            </rect>
            {/* Only label every few years to avoid overlap */}
            {(totalBars <= 10 || i === 0 || i === totalBars - 1 || i % Math.ceil(totalBars / 6) === 0) && (
              <text
                x={x + barW / 2}
                y={height - 2}
                textAnchor="middle"
                fontSize={6}
                fill="var(--ink-muted)"
              >
                {bin.year}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
