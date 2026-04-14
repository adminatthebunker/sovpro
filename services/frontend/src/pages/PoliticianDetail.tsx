import { useEffect, useState } from "react";
import { usePolitician } from "../hooks/usePolitician";
import { PoliticianDetailHeader } from "../components/PoliticianDetailHeader";
import { PoliticianSocialsTab } from "../components/PoliticianSocialsTab";
import { PoliticianOfficesTab } from "../components/PoliticianOfficesTab";
import { PoliticianTermsTab } from "../components/PoliticianTermsTab";
import { PoliticianChangesTab } from "../components/PoliticianChangesTab";
import "../styles/politician-detail.css";

type TabKey = "socials" | "offices" | "terms" | "changes";

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: "socials", label: "Socials" },
  { key: "offices", label: "Offices" },
  { key: "terms",   label: "Terms" },
  { key: "changes", label: "Changes" },
];

function parseTabFromHash(): TabKey {
  if (typeof window === "undefined") return "socials";
  const h = window.location.hash.replace(/^#/, "");
  const known: TabKey[] = ["socials", "offices", "terms", "changes"];
  return (known as string[]).includes(h) ? (h as TabKey) : "socials";
}

interface Props {
  /** Politician ID extracted from the URL by main.tsx. */
  politicianId: string;
}

export default function PoliticianDetail({ politicianId }: Props) {
  const [tab, setTab] = useState<TabKey>(() => parseTabFromHash());
  const { data, loading, error, notFound } = usePolitician(politicianId);

  // Two-way sync between active tab and URL hash so deep-links (and the
  // browser back button) keep working.
  useEffect(() => {
    const current = `#${tab}`;
    if (window.location.hash !== current) {
      window.history.replaceState(null, "", window.location.pathname + current);
    }
  }, [tab]);

  useEffect(() => {
    const onHash = () => setTab(parseTabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (loading) {
    return <div className="pol-detail pol-detail--loading">Loading politician…</div>;
  }
  if (notFound) {
    return (
      <div className="pol-detail pol-detail--empty">
        <a className="pol-detail__back" href="/">← Back to map</a>
        <h1>Politician not found</h1>
        <p>There's no active politician record with ID <code>{politicianId}</code>.</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="pol-detail pol-detail--empty">
        <a className="pol-detail__back" href="/">← Back to map</a>
        <h1>Couldn't load politician</h1>
        <p>{error.message}</p>
      </div>
    );
  }
  if (!data) return null;

  const politician = data.politician;

  return (
    <div className="pol-detail">
      <PoliticianDetailHeader politician={politician} />

      <nav className="pol-detail__tabbar" role="tablist" aria-label="Politician sections">
        {TABS.map(t => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            className={`pol-detail__tab ${tab === t.key ? "pol-detail__tab--active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <section className="pol-detail__panel" role="tabpanel" aria-label={`${tab} panel`}>
        {tab === "socials" && (
          <PoliticianSocialsTab politicianId={politicianId} politician={politician} />
        )}
        {tab === "offices" && (
          <PoliticianOfficesTab politicianId={politicianId} />
        )}
        {tab === "terms" && (
          <PoliticianTermsTab politicianId={politicianId} />
        )}
        {tab === "changes" && (
          <PoliticianChangesTab politicianId={politicianId} />
        )}
      </section>
    </div>
  );
}
