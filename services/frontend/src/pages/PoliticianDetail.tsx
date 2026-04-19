import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { usePolitician, usePoliticianOpenparliament } from "../hooks/usePolitician";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { PoliticianDetailHeader } from "../components/PoliticianDetailHeader";
import { PoliticianSocialsTab } from "../components/PoliticianSocialsTab";
import { PoliticianOfficesTab } from "../components/PoliticianOfficesTab";
import { PoliticianTermsTab } from "../components/PoliticianTermsTab";
import { PoliticianChangesTab } from "../components/PoliticianChangesTab";
import { PoliticianOpenparliamentTab } from "../components/PoliticianOpenparliamentTab";
import { PoliticianSpeechesTab } from "../components/PoliticianSpeechesTab";
import "../styles/politician-detail.css";
import "../styles/hansard-search.css";

type TabKey = "socials" | "offices" | "terms" | "changes" | "speeches" | "parliament";

const BASE_TABS: Array<{ key: TabKey; label: string }> = [
  { key: "socials", label: "Socials" },
  { key: "offices", label: "Offices" },
  { key: "terms",   label: "Terms" },
  { key: "changes", label: "Changes" },
  { key: "speeches", label: "Speeches" },
];

const ALL_TAB_KEYS: TabKey[] = ["socials", "offices", "terms", "changes", "speeches", "parliament"];

function parseTabFromHash(): TabKey {
  if (typeof window === "undefined") return "socials";
  const h = window.location.hash.replace(/^#/, "");
  return (ALL_TAB_KEYS as string[]).includes(h) ? (h as TabKey) : "socials";
}

export default function PoliticianDetail() {
  const { id } = useParams<{ id: string }>();
  const politicianId = id ?? "";
  const [tab, setTab] = useState<TabKey>(() => parseTabFromHash());
  const { data, loading, error, notFound } = usePolitician(politicianId);

  // Kick off openparliament fetch in parallel, but only for federal MPs —
  // non-federal would 400 and every click shouldn't produce a wasted round
  // trip. The hook treats null id as "don't fetch".
  const opTargetId = data?.politician?.level === "federal" ? politicianId : null;
  const op = usePoliticianOpenparliament(opTargetId);
  const showParliamentTab = !!op.data && !op.notFound;

  // Use the politician's name as the document title once loaded.
  useDocumentTitle(data?.politician?.name ?? null);

  const tabs = useMemo(() => (
    showParliamentTab
      ? [...BASE_TABS, { key: "parliament" as TabKey, label: op.loading ? "Parliament…" : "Parliament" }]
      : BASE_TABS
  ), [showParliamentTab, op.loading]);

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
        <Link className="pol-detail__back" to="/map">← Back to map</Link>
        <h1>Politician not found</h1>
        <p>There's no active politician record with ID <code>{politicianId}</code>.</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="pol-detail pol-detail--empty">
        <Link className="pol-detail__back" to="/map">← Back to map</Link>
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
        {tabs.map(t => (
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
          <PoliticianOfficesTab politicianId={politicianId} level={politician.level} />
        )}
        {tab === "terms" && (
          <PoliticianTermsTab politicianId={politicianId} />
        )}
        {tab === "changes" && (
          <PoliticianChangesTab politicianId={politicianId} />
        )}
        {tab === "speeches" && (
          <PoliticianSpeechesTab politicianId={politicianId} />
        )}
        {tab === "parliament" && showParliamentTab && (
          <PoliticianOpenparliamentTab politicianId={politicianId} />
        )}
      </section>
    </div>
  );
}
