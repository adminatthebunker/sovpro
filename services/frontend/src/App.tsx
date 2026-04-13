import { useState } from "react";
import { MapView } from "./components/MapView";
import { StatsBar } from "./components/StatsBar";
import { ReferendumSpotlight } from "./components/ReferendumSpotlight";
import { ChangesFeed } from "./components/ChangesFeed";
import { Filters, type FilterState } from "./components/Filters";
import { HeroHeadline } from "./components/HeroHeadline";
import { TierLegend } from "./components/TierLegend";
import { PartyFilter, partyColor } from "./components/PartyFilter";
import { PostalLookupBar } from "./components/PostalLookupBar";
import { PartyReportCard } from "./components/PartyReportCard";
import { Faq } from "./components/Faq";

export default function App() {
  const [filters, setFilters] = useState<FilterState>({
    layer: "all",
    level: undefined,
    province: undefined,
    party: undefined,
    includeNoData: true,
    politicianIds: undefined,
  });
  const [activeTab, setActiveTab] = useState<"map" | "referendum" | "changes" | "faq">("map");
  const [reportParty, setReportParty] = useState<string | null>(null);

  return (
    <div className="shell">
      <header className="shell__header">
        <div className="shell__brand">
          <span className="shell__logo">🍁</span>
          <div>
            <h1>SovereignWatch</h1>
            <p className="shell__tag">Where do Canadian politicians actually host their data?</p>
          </div>
        </div>
        <nav className="shell__tabs">
          <button className={activeTab === "map" ? "active" : ""} onClick={() => setActiveTab("map")}>Map</button>
          <button className={activeTab === "referendum" ? "active" : ""} onClick={() => setActiveTab("referendum")}>Referendum</button>
          <button className={activeTab === "changes" ? "active" : ""} onClick={() => setActiveTab("changes")}>Changes</button>
          <button className={activeTab === "faq" ? "active" : ""} onClick={() => setActiveTab("faq")}>FAQ</button>
        </nav>
      </header>

      {activeTab !== "referendum" && (
        <>
          <HeroHeadline />
          <StatsBar />
        </>
      )}

      {activeTab === "map" && (
        <section className="shell__map-section">
          <PostalLookupBar
            onResult={(ids) => setFilters(f => ({ ...f, politicianIds: ids ?? undefined }))}
          />
          <PartyFilter
            active={filters.party}
            onChange={(p) => setFilters({ ...filters, party: p, level: p ? "federal" : filters.level })}
            onShowReport={(p) => setReportParty(p)}
          />
          <Filters value={filters} onChange={setFilters} />
          <MapView filters={filters} />
          <TierLegend />
        </section>
      )}

      {activeTab === "referendum" && <ReferendumSpotlight />}
      {activeTab === "changes" && <ChangesFeed />}
      {activeTab === "faq" && <Faq />}

      {reportParty && (
        <PartyReportCard
          party={reportParty}
          partyColor={partyColor(reportParty)}
          onClose={() => setReportParty(null)}
        />
      )}

      <footer className="shell__footer">
        <div className="shell__footer-row">
          <span>© {new Date().getFullYear()} SovereignWatch</span>
          <span>· Built by <a href="https://bnkops.com/" target="_blank" rel="noopener noreferrer">The Bunker Operations</a></span>
          <span>· <a href="https://github.com/adminatthebunker/sovpro" target="_blank" rel="noopener noreferrer">Source on GitHub</a></span>
        </div>
        <div className="shell__footer-row shell__footer-row--muted">
          <span>Open data from <a href="https://represent.opennorth.ca" target="_blank" rel="noopener noreferrer">Open North</a> · Geolocation via MaxMind GeoLite2 · Released under the MIT license</span>
        </div>
      </footer>
    </div>
  );
}
