import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { MapView } from "../components/MapView";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { ReferendumSpotlight } from "../components/ReferendumSpotlight";
import { ChangesFeed } from "../components/ChangesFeed";
import { Filters, type FilterState } from "../components/Filters";
import { TierLegend } from "../components/TierLegend";
import { PartyFilter, partyColor } from "../components/PartyFilter";
import { PostalLookupBar, type PostalLookupResponse } from "../components/PostalLookupBar";
import { PostalResultsDrawer } from "../components/PostalResultsDrawer";
import { PartyReportCard } from "../components/PartyReportCard";
import { Faq } from "../components/Faq";

export default function MapPage() {
  useDocumentTitle("Hosting Map");
  const [searchParams] = useSearchParams();
  const postalFromUrl = searchParams.get("postal");
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
    <>
      <nav className="mapview__subtabs" aria-label="Map views">
        <button className={activeTab === "map" ? "active" : ""} onClick={() => setActiveTab("map")}>Hosting map</button>
        <button className={activeTab === "changes" ? "active" : ""} onClick={() => setActiveTab("changes")}>Recent changes</button>
        <button className={activeTab === "referendum" ? "active" : ""} onClick={() => setActiveTab("referendum")}>AB Referendum</button>
        <button className={activeTab === "faq" ? "active" : ""} onClick={() => setActiveTab("faq")}>FAQ</button>
      </nav>

      {activeTab === "map" && (
        <section className="shell__map-section">
          <div className="map-toolbar">
            <PostalLookupBar
              activePostalCode={postalResult?.postal_code ?? null}
              autoSubmitCode={postalFromUrl}
              onResult={(res, ids) => {
                setPostalResult(res);
                setFilters(f => ({ ...f, politicianIds: ids ?? undefined }));
                if (res) setReportParty(null);
              }}
            />
            <PartyFilter
              active={filters.party}
              onChange={(p) => setFilters({ ...filters, party: p, level: p ? "federal" : filters.level })}
              onShowReport={(p) => {
                setReportParty(p);
                setPostalResult(null);
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
    </>
  );
}
