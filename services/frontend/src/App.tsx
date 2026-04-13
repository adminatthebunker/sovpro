import { useState } from "react";
import { MapView } from "./components/MapView";
import { StatsBar } from "./components/StatsBar";
import { ReferendumSpotlight } from "./components/ReferendumSpotlight";
import { ChangesFeed } from "./components/ChangesFeed";
import { Filters, type FilterState } from "./components/Filters";
import { HeroHeadline } from "./components/HeroHeadline";
import { TierLegend } from "./components/TierLegend";

export default function App() {
  const [filters, setFilters] = useState<FilterState>({
    layer: "all",
    level: undefined,
    province: undefined,
  });
  const [activeTab, setActiveTab] = useState<"map" | "referendum" | "changes">("map");

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
        </nav>
      </header>

      <HeroHeadline />
      <StatsBar />

      {activeTab === "map" && (
        <section className="shell__map-section">
          <Filters value={filters} onChange={setFilters} />
          <MapView filters={filters} />
          <TierLegend />
        </section>
      )}

      {activeTab === "referendum" && <ReferendumSpotlight />}
      {activeTab === "changes" && <ChangesFeed />}

      <footer className="shell__footer">
        <span>Open data from <a href="https://represent.opennorth.ca">Open North</a> · Geolocation via MaxMind GeoLite2</span>
        <span>· <a href="https://github.com/">Source</a></span>
      </footer>
    </div>
  );
}
