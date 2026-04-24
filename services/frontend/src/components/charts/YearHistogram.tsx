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
 *  shifts visually honest. HTML/CSS so text stays crisp at any width. */
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
  const totalBars = filled.length;
  const labelStride = Math.max(1, Math.ceil(totalBars / 6));

  return (
    <div className="year-hist" style={{ height }} role="img" aria-label={`Bar chart of counts by year from ${minYear} to ${maxYear}`}>
      <div className="year-hist__bars">
        {filled.map((bin) => {
          const pct = bin.count === 0 ? 0 : (bin.count / maxCount) * 100;
          return (
            <div
              key={bin.year}
              className={`year-hist__bar ${bin.count === 0 ? "year-hist__bar--empty" : ""}`}
              style={{ height: `${Math.max(pct, bin.count === 0 ? 2 : 1)}%` }}
              title={`${bin.year}: ${bin.count.toLocaleString()}`}
            />
          );
        })}
      </div>
      <div className="year-hist__labels" aria-hidden="true">
        {filled.map((bin, i) => {
          const show = totalBars <= 10 || i === 0 || i === totalBars - 1 || i % labelStride === 0;
          return (
            <div key={bin.year} className="year-hist__label">
              {show ? bin.year : ""}
            </div>
          );
        })}
      </div>
    </div>
  );
}
