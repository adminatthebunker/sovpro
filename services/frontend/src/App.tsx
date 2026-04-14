import { useEffect, useState } from "react";
import { MapView } from "./components/MapView";
import { StatsBar } from "./components/StatsBar";
import { ReferendumSpotlight } from "./components/ReferendumSpotlight";
import { ChangesFeed } from "./components/ChangesFeed";
import { Filters, type FilterState } from "./components/Filters";
import { HeroHeadline } from "./components/HeroHeadline";
import { TierLegend } from "./components/TierLegend";
import { PartyFilter, partyColor } from "./components/PartyFilter";
import { PostalLookupBar, type PostalLookupResponse } from "./components/PostalLookupBar";
import { PostalResultsDrawer } from "./components/PostalResultsDrawer";
import { PartyReportCard } from "./components/PartyReportCard";
import { Faq } from "./components/Faq";
import { WhoWeTrack } from "./components/WhoWeTrack";
import { ShareMenu } from "./components/ShareMenu";

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
  const [postalResult, setPostalResult] = useState<PostalLookupResponse | null>(null);

  return (
    <div className="shell">
      <header className="shell__header">
        <div className="shell__brand">
          <span className="shell__logo">🍁</span>
          <div>
            <h1>Canadian Political Data</h1>
            <p className="shell__tag">Where do Canadian politicians actually host their data?</p>
          </div>
        </div>
        <nav className="shell__tabs">
          <button className={activeTab === "map" ? "active" : ""} onClick={() => setActiveTab("map")}>Map</button>
          <button className={activeTab === "changes" ? "active" : ""} onClick={() => setActiveTab("changes")}>Changes</button>
          <button className={activeTab === "faq" ? "active" : ""} onClick={() => setActiveTab("faq")}>FAQ</button>
          <button className={activeTab === "referendum" ? "active" : ""} onClick={() => setActiveTab("referendum")}>AB Referendum</button>
          <ShareMenu />
          <a
            className="shell__contact"
            href="mailto:admin@thebunkerops.ca?subject=CanadianPoliticalData%20feedback"
            title="Send feedback by email"
          >
            ✉ Contact
          </a>
        </nav>
      </header>

      {activeTab !== "referendum" && (
        <>
          <div className="hero-row">
            <HeroHeadline />
            <WhoWeTrack />
          </div>
          <StatsBar />
        </>
      )}

      {activeTab === "map" && (
        <section className="shell__map-section">
          <div className="map-toolbar">
            <PostalLookupBar
              activePostalCode={postalResult?.postal_code ?? null}
              onResult={(res, ids) => {
                setPostalResult(res);
                setFilters(f => ({ ...f, politicianIds: ids ?? undefined }));
                if (res) setReportParty(null);  // mutually exclusive with party report
              }}
            />
            <PartyFilter
              active={filters.party}
              onChange={(p) => setFilters({ ...filters, party: p, level: p ? "federal" : filters.level })}
              onShowReport={(p) => {
                setReportParty(p);
                setPostalResult(null);  // mutually exclusive with postal result
                setFilters(f => ({ ...f, politicianIds: undefined }));
              }}
            />
            <Filters value={filters} onChange={setFilters} />
          </div>
          <p className="map-hint" role="note">
            💡 Click any riding to see the politician&apos;s full hosting profile, socials, and offices.
          </p>
          <div className={`map-with-drawer ${(reportParty || postalResult) ? "is-open" : ""}`}>
            <div className="map-with-drawer__map">
              <MapView filters={filters} />
            </div>
            {postalResult && (
              <PostalResultsDrawer
                data={postalResult}
                onClose={() => {
                  setPostalResult(null);
                  setFilters(f => ({ ...f, politicianIds: undefined }));
                }}
              />
            )}
            {!postalResult && reportParty && (
              <PartyReportCard
                party={reportParty}
                partyColor={partyColor(reportParty)}
                onClose={() => setReportParty(null)}
              />
            )}
          </div>
          <TierLegend />
        </section>
      )}

      {activeTab === "referendum" && (
        <ReferendumSpotlight
          reportParty={reportParty}
          onShowReport={(p) => setReportParty(p)}
          onCloseReport={() => setReportParty(null)}
        />
      )}
      {activeTab === "changes" && <ChangesFeed />}
      {activeTab === "faq" && <Faq />}

      <footer className="shell__footer">
        <div className="shell__footer-row">
          <span>© {new Date().getFullYear()} Canadian Political Data</span>
          <span>· Built by <a href="https://bnkops.com/" target="_blank" rel="noopener noreferrer">The Bunker Operations</a></span>
          <span>· <a href="https://github.com/adminatthebunker/CanadianPoliticalData" target="_blank" rel="noopener noreferrer">Source on GitHub</a></span>
          <span>· <a href="mailto:admin@thebunkerops.ca?subject=CanadianPoliticalData%20feedback">Contact &amp; feedback</a></span>
        </div>
        <div className="shell__footer-row shell__footer-row--muted">
          <span>Open data from <a href="https://represent.opennorth.ca" target="_blank" rel="noopener noreferrer">Open North</a> · Geolocation via MaxMind GeoLite2 · Released under the MIT license</span>
        </div>
      </footer>
    </div>
  );
}
