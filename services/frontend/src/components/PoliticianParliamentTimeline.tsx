import { useMemo } from "react";
import {
  usePoliticianParliamentActivity,
  type OpenparliamentSpeech,
  type OpenparliamentBill,
} from "../hooks/usePolitician";

const OPENPARL_SITE = "https://openparliament.ca";

type TimelineItem =
  | { kind: "speech"; time: number; speech: OpenparliamentSpeech }
  | { kind: "bill"; time: number; bill: OpenparliamentBill };

// Ascending order from most-recent to least-recent. Buckets earlier in the
// array win — the first matching bucket claims the item.
const BUCKETS: Array<{ key: string; label: string; maxDaysAgo: number }> = [
  { key: "today",      label: "Today",          maxDaysAgo: 1 },
  { key: "week",       label: "This week",      maxDaysAgo: 7 },
  { key: "two-weeks",  label: "Two weeks ago",  maxDaysAgo: 14 },
  { key: "month",      label: "A month ago",    maxDaysAgo: 31 },
  { key: "six-months", label: "Six months ago", maxDaysAgo: 183 },
  { key: "year",       label: "A year ago",     maxDaysAgo: 365 },
  { key: "older",      label: "Older",          maxDaysAgo: Infinity },
];

function bucketFor(itemTime: number, now: number): string {
  const daysAgo = (now - itemTime) / (1000 * 60 * 60 * 24);
  for (const b of BUCKETS) {
    if (daysAgo <= b.maxDaysAgo) return b.key;
  }
  return "older";
}

function labelFor(key: string): string {
  return BUCKETS.find(b => b.key === key)?.label ?? key;
}

/** Strip HTML tags and collapse whitespace for a short preview snippet. */
function snippet(html: string | undefined, maxLen: number = 180): string {
  if (!html) return "";
  const text = html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen).trimEnd() + "…";
}

function bilingual(v: Record<string, string> | undefined): string {
  if (!v) return "";
  return v.en ?? v.fr ?? Object.values(v)[0] ?? "";
}

function toSiteUrl(apiPath: string | undefined): string | null {
  if (!apiPath) return null;
  if (apiPath.startsWith("http")) return apiPath;
  return `${OPENPARL_SITE}${apiPath}`;
}

function formatDate(ms: number): string {
  return new Date(ms).toLocaleDateString("en-CA", {
    year: "numeric", month: "short", day: "numeric",
  });
}

interface Props {
  politicianId: string;
}

export function PoliticianParliamentTimeline({ politicianId }: Props) {
  const { data, loading, error, notFound } = usePoliticianParliamentActivity(politicianId);

  const grouped = useMemo(() => {
    if (!data?.data) return null;
    const now = Date.now();
    const items: TimelineItem[] = [];
    for (const s of data.data.speeches) {
      const t = Date.parse(s.time);
      if (!isNaN(t)) items.push({ kind: "speech", time: t, speech: s });
    }
    for (const b of data.data.bills) {
      if (!b.introduced) continue;
      const t = Date.parse(b.introduced);
      if (!isNaN(t)) items.push({ kind: "bill", time: t, bill: b });
    }
    items.sort((a, b) => b.time - a.time);

    const byBucket = new Map<string, TimelineItem[]>();
    for (const it of items) {
      const b = bucketFor(it.time, now);
      let arr = byBucket.get(b);
      if (!arr) { arr = []; byBucket.set(b, arr); }
      arr.push(it);
    }
    return Array.from(byBucket.entries());
  }, [data]);

  if (loading) {
    return <div className="pol-parl-timeline__loading">Loading speeches and bills from openparliament.ca…</div>;
  }
  if (notFound || error || !data) {
    return null; // Fail silently — the main Parliament tab content still renders
  }

  const totalItems = (data.data.speeches.length ?? 0) + (data.data.bills.length ?? 0);
  if (totalItems === 0) {
    return (
      <div className="pol-parl-timeline__empty">
        openparliament.ca has no recorded speeches or bills for this MP yet.
      </div>
    );
  }

  return (
    <div className="pol-parl-timeline">
      {data.warning && (
        <div className="pol-parl-timeline__warning">⚠ {data.warning}</div>
      )}

      {grouped?.map(([bucketKey, items]) => (
        <section key={bucketKey} className="pol-parl-timeline__bucket">
          <h4 className="pol-parl-timeline__bucket-label">{labelFor(bucketKey)}</h4>
          <ul className="pol-parl-timeline__list">
            {items.map((item, i) => {
              if (item.kind === "speech") {
                const s = item.speech;
                const url = toSiteUrl(s.url);
                const topic = bilingual(s.h2) || bilingual(s.h1);
                const h1 = bilingual(s.h1);
                const preview = snippet(s.content?.en ?? bilingual(s.content));
                return (
                  <li key={`s-${i}`} className="pol-parl-timeline__item pol-parl-timeline__item--speech">
                    <div className="pol-parl-timeline__kind">
                      <span className="pol-parl-timeline__badge pol-parl-timeline__badge--speech">Spoke</span>
                      <time>{formatDate(item.time)}</time>
                    </div>
                    <div className="pol-parl-timeline__body">
                      <div className="pol-parl-timeline__headline">
                        {url ? (
                          <a href={url} target="_blank" rel="noopener noreferrer">{topic || "in the House"}</a>
                        ) : (
                          topic || "in the House"
                        )}
                        {h1 && h1 !== topic && (
                          <span className="pol-parl-timeline__context"> · {h1}</span>
                        )}
                      </div>
                      {preview && <p className="pol-parl-timeline__preview">{preview}</p>}
                    </div>
                  </li>
                );
              }
              const b = item.bill;
              const url = toSiteUrl(b.url);
              const name = bilingual(b.name);
              return (
                <li key={`b-${i}`} className="pol-parl-timeline__item pol-parl-timeline__item--bill">
                  <div className="pol-parl-timeline__kind">
                    <span className="pol-parl-timeline__badge pol-parl-timeline__badge--bill">Sponsored</span>
                    <time>{formatDate(item.time)}</time>
                  </div>
                  <div className="pol-parl-timeline__body">
                    <div className="pol-parl-timeline__headline">
                      {url ? (
                        <a href={url} target="_blank" rel="noopener noreferrer">
                          Bill {b.number ?? ""} {name ? `— ${name}` : ""}
                        </a>
                      ) : (
                        <>Bill {b.number ?? ""} {name ? `— ${name}` : ""}</>
                      )}
                      {b.session && <span className="pol-parl-timeline__context"> · session {b.session}</span>}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
