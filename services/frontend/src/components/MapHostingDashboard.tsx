import { useFetch } from "../hooks/useFetch";
import type { ReferendumSideSummary, StatsResponse } from "../types";
import { COUNTRY_FLAGS } from "../types";

interface RefResponse {
  leave_side: ReferendumSideSummary;
  stay_side: ReferendumSideSummary;
  irony_score: string;
}

export function MapHostingDashboard() {
  const { data: stats } = useFetch<StatsResponse>("/stats");
  const { data: ref } = useFetch<RefResponse>("/stats/referendum");

  if (!stats) {
    return (
      <section className="map-dashboard map-dashboard--loading" aria-busy="true">
        <div className="map-dashboard__intro">
          <h2 className="map-dashboard__heading">Where Canadian politicians store your data</h2>
          <p className="map-dashboard__sub">Loading hosting stats…</p>
        </div>
      </section>
    );
  }

  const pctOutside = stats.politicians.pct_not_canadian ?? 0;
  const tierCounts = stats.politicians.sovereignty ?? {};
  const tier2 = tierCounts.tier_2 ?? 0;
  const foreignSites =
    (tierCounts.tier_3 ?? 0) + (tierCounts.tier_4 ?? 0) + (tierCounts.tier_5 ?? 0);
  const topForeign = stats.top_foreign_locations?.[0];

  const leaveSide = ref?.leave_side ?? stats.organizations.referendum.leave;
  const staySide = ref?.stay_side ?? stats.organizations.referendum.stay;
  const leaveOutsideAB = Math.max(
    0,
    (leaveSide?.total_websites ?? 0) - (leaveSide?.hosted_in_alberta ?? 0)
  );
  const stayOutsideAB = Math.max(
    0,
    (staySide?.total_websites ?? 0) - (staySide?.hosted_in_alberta ?? 0)
  );

  const fallbackIrony =
    leaveSide && staySide && leaveSide.total_websites > 0 && staySide.total_websites > 0
      ? "Alberta’s sovereignty debate has two sides. Neither hosts their website data inside Alberta."
      : null;
  const irony = ref?.irony_score || fallbackIrony;

  return (
    <section className="map-dashboard" aria-label="Hosting sovereignty dashboard">
      <div className="map-dashboard__intro">
        <h2 className="map-dashboard__heading">Where Canadian politicians store your data</h2>
        <p className="map-dashboard__sub">
          The people who write laws about your data, and the groups fighting over Alberta’s
          sovereignty, mostly keep their websites on foreign servers.
        </p>
      </div>

      <div className="statsbar map-dashboard__cards">
        <div className="statcard statcard--bad" title="Tiers 3–5: CDN-fronted, US-hosted, or other foreign hosting. Excludes shared parliamentary infrastructure.">
          <div className="statcard__rail" aria-hidden />
          <div className="statcard__icon" aria-hidden>✈️</div>
          <div className="statcard__body">
            <div className="statcard__value">{pctOutside.toFixed(1)}%</div>
            <div className="statcard__label">
              of politicians’ websites store user data outside Canada
              {foreignSites > 0 ? (
                <span className="map-dashboard__foot"> · {foreignSites.toLocaleString()} sites</span>
              ) : null}
            </div>
          </div>
        </div>

        {topForeign && (
          <div
            className="statcard statcard--warn"
            title={`${topForeign.city}, ${topForeign.country} hosts ${topForeign.n} Canadian politicians’ sites`}
          >
            <div className="statcard__rail" aria-hidden />
            <div className="statcard__icon" aria-hidden>{COUNTRY_FLAGS[topForeign.country] ?? "🌐"}</div>
            <div className="statcard__body">
              <div className="statcard__value">
                {topForeign.city}
                <span className="statcard__sub"> {topForeign.country}</span>
              </div>
              <div className="statcard__label">
                top foreign city hosting Canadian politicians’ data ({topForeign.n} sites)
              </div>
            </div>
          </div>
        )}

        <div
          className="statcard statcard--warn"
          title="Data physically in Canada, but on servers owned by American or other foreign companies (AWS, Cloudflare, Shopify, etc.)."
        >
          <div className="statcard__rail" aria-hidden />
          <div className="statcard__icon" aria-hidden>🇨🇦</div>
          <div className="statcard__body">
            <div className="statcard__value">{tier2.toLocaleString()}</div>
            <div className="statcard__label">
              sites on Canadian soil, but hosted by foreign-owned providers
            </div>
          </div>
        </div>

        {(leaveSide?.total_websites ?? 0) + (staySide?.total_websites ?? 0) > 0 && (
          <div
            className="statcard statcard--bad"
            title="Alberta referendum organizations — the 'Leave Canada' and 'Stay in Canada' camps — and how many of their websites are hosted outside the province they're fighting over."
          >
            <div className="statcard__rail" aria-hidden />
            <div className="statcard__icon" aria-hidden>🤦</div>
            <div className="statcard__body">
              <div className="statcard__value statcard__value--sm statcard__split">
                <div className="statcard__split-half">
                  <span>{leaveOutsideAB}/{leaveSide?.total_websites ?? 0}</span>
                  <span className="statcard__split-label">Leave</span>
                </div>
                <span className="statcard__split-divider" aria-hidden>·</span>
                <div className="statcard__split-half">
                  <span>{stayOutsideAB}/{staySide?.total_websites ?? 0}</span>
                  <span className="statcard__split-label">Stay</span>
                </div>
              </div>
              <div className="statcard__label">
                Alberta referendum orgs hosting <em>outside</em> Alberta
              </div>
            </div>
          </div>
        )}
      </div>

      {irony && <p className="map-dashboard__irony">{irony}</p>}
    </section>
  );
}
