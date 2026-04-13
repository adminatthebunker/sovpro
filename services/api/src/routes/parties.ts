import type { FastifyInstance } from "fastify";
import { query, queryOne } from "../db.js";

/**
 * /api/v1/parties — list of parties + their headline counts.
 * /api/v1/parties/:name/report — full report card for one party.
 *
 * The "report card" computes a letter grade based on the share of the
 * party's MPs whose personal/campaign sites are physically hosted in
 * Canada (tiers 1+2). Sites are deduplicated by hostname so a party
 * doesn't get penalized twice when multiple MPs share the same
 * party-managed subdomain pattern.
 */

function letterGrade(pctCanadian: number): { grade: string; gradeClass: string } {
  if (pctCanadian >= 85) return { grade: "A",  gradeClass: "a" };
  if (pctCanadian >= 70) return { grade: "B",  gradeClass: "b" };
  if (pctCanadian >= 50) return { grade: "C",  gradeClass: "c" };
  if (pctCanadian >= 30) return { grade: "D",  gradeClass: "d" };
  return                 { grade: "F",  gradeClass: "f" };
}

export default async function partyRoutes(app: FastifyInstance) {
  // Index: every party with at least one tracked website
  app.get("/", async () => {
    const rows = await query<{
      party: string; politicians: number; sites: number;
      personal: number; party_managed: number;
      ca: number; ab: number; us: number; cdn: number; foreign: number;
    }>(
      `SELECT party,
              COUNT(DISTINCT politician_id)::int AS politicians,
              COUNT(DISTINCT website_id)::int AS sites,
              COUNT(DISTINCT website_id) FILTER (WHERE site_class = 'personal')::int AS personal,
              COUNT(DISTINCT website_id) FILTER (WHERE site_class = 'party_managed')::int AS party_managed,
              COUNT(*) FILTER (WHERE sovereignty_tier IN (1,2))::int AS ca,
              COUNT(*) FILTER (WHERE ip_region = 'Alberta')::int AS ab,
              COUNT(*) FILTER (WHERE sovereignty_tier = 4)::int AS us,
              COUNT(*) FILTER (WHERE sovereignty_tier = 3)::int AS cdn,
              COUNT(*) FILTER (WHERE sovereignty_tier = 5)::int AS foreign
       FROM map_politicians
       WHERE party IS NOT NULL AND party <> ''
       GROUP BY party
       ORDER BY politicians DESC`
    );
    return { parties: rows };
  });

  app.get("/:name/report", async (req, reply) => {
    const name = decodeURIComponent((req.params as { name: string }).name);

    // Per-party stats — dedup by hostname so shared party-managed subdomains
    // count once. (Personal sites are per-MP and naturally distinct.)
    const headRow = await queryOne<{
      politicians: number; sites: number;
      personal_sites: number; party_managed_sites: number;
      ca: number; cdn: number; us: number; foreign: number;
      no_website: number;
    }>(
      `WITH per_site AS (
         SELECT DISTINCT ON (hostname) hostname, sovereignty_tier, site_class
         FROM map_politicians
         WHERE party = $1
         ORDER BY hostname, scanned_at DESC NULLS LAST
       )
       SELECT
         (SELECT COUNT(*)::int FROM politicians WHERE party = $1 AND is_active = true) AS politicians,
         (SELECT COUNT(*)::int FROM per_site) AS sites,
         (SELECT COUNT(*)::int FROM per_site WHERE site_class = 'personal') AS personal_sites,
         (SELECT COUNT(*)::int FROM per_site WHERE site_class = 'party_managed') AS party_managed_sites,
         (SELECT COUNT(*)::int FROM per_site WHERE sovereignty_tier IN (1,2)) AS ca,
         (SELECT COUNT(*)::int FROM per_site WHERE sovereignty_tier = 3) AS cdn,
         (SELECT COUNT(*)::int FROM per_site WHERE sovereignty_tier = 4) AS us,
         (SELECT COUNT(*)::int FROM per_site WHERE sovereignty_tier = 5) AS foreign,
         (SELECT COUNT(*)::int FROM politicians p WHERE p.party = $1 AND p.is_active = true
           AND NOT EXISTS (SELECT 1 FROM map_politicians mp WHERE mp.politician_id = p.id)) AS no_website`,
      [name]
    );
    if (!headRow || headRow.politicians === 0) {
      return reply.notFound(`No tracked politicians for party "${name}"`);
    }

    const sites = headRow.sites;
    const ca = headRow.ca;
    const pctCa = sites > 0 ? Math.round(100 * ca / sites) : 0;
    const { grade, gradeClass } = letterGrade(pctCa);

    // Top providers (deduped by hostname)
    const topProviders = await query<{ provider: string; n: number }>(
      `WITH per_site AS (
         SELECT DISTINCT ON (hostname) hostname, hosting_provider
         FROM map_politicians WHERE party = $1
         ORDER BY hostname, scanned_at DESC NULLS LAST
       )
       SELECT hosting_provider AS provider, COUNT(*)::int AS n
       FROM per_site WHERE hosting_provider IS NOT NULL
       GROUP BY hosting_provider ORDER BY n DESC LIMIT 6`,
      [name]
    );

    // Top destinations outside Canada
    const topForeignCities = await query<{ city: string; country: string; n: number }>(
      `WITH per_site AS (
         SELECT DISTINCT ON (hostname) hostname, ip_city, ip_country
         FROM map_politicians WHERE party = $1
         ORDER BY hostname, scanned_at DESC NULLS LAST
       )
       SELECT ip_city AS city, ip_country AS country, COUNT(*)::int AS n
       FROM per_site WHERE ip_city IS NOT NULL AND ip_country <> 'CA'
       GROUP BY ip_city, ip_country ORDER BY n DESC LIMIT 5`,
      [name]
    );

    // Best/worst MPs by sovereignty
    const bestMps = await query<{ name: string; constituency_name: string | null; tier: number }>(
      `SELECT DISTINCT ON (politician_id)
              name, constituency_name, sovereignty_tier AS tier
       FROM map_politicians
       WHERE party = $1 AND sovereignty_tier IN (1, 2)
       ORDER BY politician_id, sovereignty_tier ASC LIMIT 5`,
      [name]
    );
    const worstMps = await query<{ name: string; constituency_name: string | null; tier: number; provider: string | null; city: string | null; country: string | null }>(
      `SELECT DISTINCT ON (politician_id)
              name, constituency_name, sovereignty_tier AS tier,
              hosting_provider AS provider, ip_city AS city, ip_country AS country
       FROM map_politicians
       WHERE party = $1 AND sovereignty_tier IN (4, 5)
       ORDER BY politician_id, sovereignty_tier DESC LIMIT 5`,
      [name]
    );

    reply.header("cache-control", "public, max-age=120");
    return {
      party: name,
      grade,
      grade_class: gradeClass,
      pct_canadian: pctCa,
      politicians: headRow.politicians,
      sites,
      personal_sites: headRow.personal_sites,
      party_managed_sites: headRow.party_managed_sites,
      no_website: headRow.no_website,
      breakdown: {
        canadian: ca,
        cdn: headRow.cdn,
        us: headRow.us,
        foreign: headRow.foreign,
      },
      top_providers: topProviders,
      top_foreign_locations: topForeignCities,
      best_mps: bestMps,
      worst_mps: worstMps,
    };
  });
}
