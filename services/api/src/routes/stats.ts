import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

export default async function statsRoutes(app: FastifyInstance) {
  app.get("/", async () => {
    // ── Politicians totals + tiers ──────────────────────────
    const totalRow = (await query<{ total: number }>(
      `SELECT COUNT(*)::int AS total FROM politicians WHERE is_active=true`))[0];
    const byLevel = await query<{ level: string; n: number }>(
      `SELECT level, COUNT(*)::int AS n FROM politicians WHERE is_active=true GROUP BY level ORDER BY level`);
    const byParty = await query<{ party: string; n: number }>(
      `SELECT party, COUNT(*)::int AS n FROM politicians WHERE is_active=true AND party IS NOT NULL
       GROUP BY party ORDER BY n DESC LIMIT 20`);

    // Headline stats: unique hostnames, EXCLUDING shared_official infrastructure
    // (ourcommons.ca, assembly.ab.ca, city council pages). Those are shared
    // institutional sites — not a personal political choice — so they're
    // surfaced separately as `parliamentary_infrastructure`.
    const polTiers = await query<{ tier: number; n: number }>(
      `WITH uniq AS (
         SELECT DISTINCT ON (mp.hostname) mp.sovereignty_tier
         FROM map_politicians mp
         JOIN websites w ON w.id = mp.website_id
         WHERE mp.sovereignty_tier IS NOT NULL
           AND COALESCE(w.label, '') <> 'shared_official'
         ORDER BY mp.hostname, mp.scanned_at DESC
       )
       SELECT sovereignty_tier AS tier, COUNT(*)::int AS n
       FROM uniq GROUP BY sovereignty_tier ORDER BY sovereignty_tier`);

    const notCanadian = await query<{ pct: number }>(
      `WITH uniq AS (
         SELECT DISTINCT ON (mp.hostname) mp.sovereignty_tier
         FROM map_politicians mp
         JOIN websites w ON w.id = mp.website_id
         WHERE mp.sovereignty_tier IS NOT NULL
           AND COALESCE(w.label, '') <> 'shared_official'
         ORDER BY mp.hostname, mp.scanned_at DESC
       )
       SELECT COALESCE(
         100.0 * SUM(CASE WHEN sovereignty_tier IN (3,4,5) THEN 1 ELSE 0 END)
               / NULLIF(COUNT(*),0),
         0)::float AS pct
       FROM uniq`);

    const personalTiers = await query<{ tier: number; n: number }>(
      `WITH uniq AS (
         SELECT DISTINCT ON (mp.hostname) mp.sovereignty_tier
         FROM map_politicians mp
         JOIN websites w ON w.id = mp.website_id
         WHERE mp.sovereignty_tier IS NOT NULL AND w.label = 'personal'
         ORDER BY mp.hostname, mp.scanned_at DESC
       )
       SELECT sovereignty_tier AS tier, COUNT(*)::int AS n
       FROM uniq GROUP BY sovereignty_tier ORDER BY sovereignty_tier`);

    // Footnote dataset: where the shared parliamentary infra lives.
    const sharedInfra = await query<{
      hostname: string; ip_country: string | null; ip_city: string | null;
      hosting_provider: string | null; sovereignty_tier: number; counted_for: number;
    }>(
      `SELECT mp.hostname, mp.ip_country, mp.ip_city, mp.hosting_provider,
              mp.sovereignty_tier, COUNT(*)::int AS counted_for
       FROM map_politicians mp
       JOIN websites w ON w.id = mp.website_id
       WHERE w.label = 'shared_official'
       GROUP BY mp.hostname, mp.ip_country, mp.ip_city, mp.hosting_provider, mp.sovereignty_tier
       ORDER BY counted_for DESC`);

    // Top cities / providers — exclude shared parliamentary infra and dedup
    // by hostname so ourcommons.ca / assembly.ab.ca don't dominate.
    const topCities = await query<{ city: string; country: string; n: number }>(
      `WITH uniq AS (
         SELECT DISTINCT ON (w.hostname) s.ip_city, s.ip_country
         FROM websites w
         JOIN infrastructure_scans s ON s.website_id = w.id
         WHERE COALESCE(w.label,'') <> 'shared_official'
         ORDER BY w.hostname, s.scanned_at DESC
       )
       SELECT ip_city AS city, ip_country AS country, COUNT(*)::int AS n
       FROM uniq WHERE ip_city IS NOT NULL
       GROUP BY ip_city, ip_country ORDER BY n DESC LIMIT 10`);

    // Top destination outside Canada — answers "where does the data go?"
    const topForeignCities = await query<{ city: string; country: string; n: number }>(
      `WITH uniq AS (
         SELECT DISTINCT ON (w.hostname) s.ip_city, s.ip_country
         FROM websites w
         JOIN infrastructure_scans s ON s.website_id = w.id
         WHERE COALESCE(w.label,'') <> 'shared_official'
         ORDER BY w.hostname, s.scanned_at DESC
       )
       SELECT ip_city AS city, ip_country AS country, COUNT(*)::int AS n
       FROM uniq WHERE ip_city IS NOT NULL AND ip_country <> 'CA'
       GROUP BY ip_city, ip_country ORDER BY n DESC LIMIT 5`);
    const topProviders = await query<{ provider: string; n: number }>(
      `WITH uniq AS (
         SELECT DISTINCT ON (w.hostname) s.hosting_provider
         FROM websites w
         JOIN infrastructure_scans s ON s.website_id = w.id
         WHERE COALESCE(w.label,'') <> 'shared_official'
         ORDER BY w.hostname, s.scanned_at DESC
       )
       SELECT hosting_provider AS provider, COUNT(*)::int AS n
       FROM uniq WHERE hosting_provider IS NOT NULL
       GROUP BY hosting_provider ORDER BY n DESC LIMIT 10`);

    // ── Phase 7a: nationwide headline additions ────────────
    // These are additive — existing fields above are untouched.
    const polsByProvince = await query<{ province: string | null; n: number }>(
      `SELECT province_territory AS province, COUNT(*)::int AS n
         FROM politicians
        WHERE is_active = true
        GROUP BY province_territory`);
    const polsByLevel = await query<{ level: string; n: number }>(
      `SELECT level, COUNT(*)::int AS n
         FROM politicians
        WHERE is_active = true
        GROUP BY level`);

    // Social media adoption across active politicians only.
    const socialsByPlatform = await query<{ platform: string; n: number }>(
      `SELECT ps.platform, COUNT(DISTINCT ps.politician_id)::int AS n
         FROM politician_socials ps
         JOIN politicians p ON p.id = ps.politician_id
        WHERE p.is_active = true
        GROUP BY ps.platform
        ORDER BY n DESC`);
    const socialsTotals = (await query<{
      total_with_any: number; total_without: number; dead_pct: number;
    }>(
      `WITH active_pols AS (
         SELECT id FROM politicians WHERE is_active = true
       ),
       with_any AS (
         SELECT DISTINCT politician_id
           FROM politician_socials
          WHERE politician_id IN (SELECT id FROM active_pols)
       ),
       all_rows AS (
         SELECT ps.is_live
           FROM politician_socials ps
           JOIN active_pols a ON a.id = ps.politician_id
       )
       SELECT
         (SELECT COUNT(*) FROM with_any)::int                                AS total_with_any,
         ((SELECT COUNT(*) FROM active_pols)
           - (SELECT COUNT(*) FROM with_any))::int                           AS total_without,
         COALESCE(
           100.0 * SUM(CASE WHEN is_live = false THEN 1 ELSE 0 END)
                 / NULLIF(COUNT(*), 0),
           0)::float                                                         AS dead_pct
         FROM all_rows`
    ))[0];

    const recentChanges24h = (await query<{ n: number }>(
      `SELECT COUNT(*)::int AS n
         FROM politician_changes
        WHERE detected_at > now() - interval '24 hours'`))[0];

    const topPartiesBySeatCount = await query<{ party: string; n: number }>(
      `SELECT party, COUNT(*)::int AS n
         FROM politicians
        WHERE is_active = true AND party IS NOT NULL
        GROUP BY party
        ORDER BY n DESC
        LIMIT 10`);

    // Shape: { AB: 92, BC: 5, ..., total: N }
    const politiciansByProvince: Record<string, number> = {};
    let politiciansByProvinceTotal = 0;
    for (const r of polsByProvince) {
      const key = r.province ?? "unknown";
      politiciansByProvince[key] = r.n;
      politiciansByProvinceTotal += r.n;
    }
    politiciansByProvince.total = politiciansByProvinceTotal;

    const politiciansByLevel: Record<string, number> = {};
    for (const r of polsByLevel) politiciansByLevel[r.level] = r.n;

    // ── Dataset depth counts (Phase 7a tail) ───────────────
    const officesCount = (await query<{ n: number }>(
      `SELECT COUNT(*)::int AS n FROM politician_offices`))[0]?.n ?? 0;
    const committeesCount = (await query<{ n: number }>(
      `SELECT COUNT(*)::int AS n FROM politician_committees`))[0]?.n ?? 0;
    const socialHandlesTotal = (await query<{ n: number }>(
      `SELECT COUNT(*)::int AS n FROM politician_socials`))[0]?.n ?? 0;

    // ── Organizations totals + referendum breakdown ─────────
    const orgTotal = (await query<{ n: number }>(
      `SELECT COUNT(*)::int AS n FROM organizations WHERE is_active=true`))[0];

    const refLeave = await refSideSummary("leave");
    const refStay = await refSideSummary("stay");

    return {
      politicians: {
        total: totalRow?.total ?? 0,
        by_level: Object.fromEntries(byLevel.map(r => [r.level, r.n])),
        by_party: byParty,
        // Headline numbers exclude shared parliamentary infrastructure
        // (ourcommons.ca, assembly.ab.ca, city council pages).
        sovereignty: Object.fromEntries(polTiers.map(r => [`tier_${r.tier}`, r.n])),
        sovereignty_personal: Object.fromEntries(personalTiers.map(r => [`tier_${r.tier}`, r.n])),
        pct_not_canadian: Math.round((notCanadian[0]?.pct ?? 0) * 10) / 10,
      },
      parliamentary_infrastructure: {
        note: "Shared institutional sites (ourcommons.ca, assembly.ab.ca, city council pages) are excluded from the headline stats. They are shared infrastructure, not personal political choices.",
        sites: sharedInfra,
      },
      organizations: {
        total: orgTotal?.n ?? 0,
        referendum: { leave: refLeave, stay: refStay },
      },
      top_server_locations: topCities,
      top_foreign_locations: topForeignCities,
      top_providers: topProviders,
      // ── Phase 7a headlines ─────────────────────────────
      politicians_by_province: politiciansByProvince,
      politicians_by_level: politiciansByLevel,
      socials_adoption: {
        by_platform: Object.fromEntries(socialsByPlatform.map(r => [r.platform, r.n])),
        total_with_any: socialsTotals?.total_with_any ?? 0,
        total_without: socialsTotals?.total_without ?? 0,
      },
      dead_socials_pct: Math.round((socialsTotals?.dead_pct ?? 0) * 10) / 10,
      recent_changes_24h: recentChanges24h?.n ?? 0,
      top_parties_by_seat_count: topPartiesBySeatCount,
      dataset_depth: {
        offices_mapped: officesCount,
        committees_tracked: committeesCount,
        social_handles_total: socialHandlesTotal,
      },
    };
  });

  // Dedicated referendum stats
  app.get("/referendum", async () => {
    const leave = await refSideSummary("leave", true);
    const stay = await refSideSummary("stay", true);

    const ironyScore = buildIronyScore(leave, stay);

    return {
      leave_side: leave,
      stay_side: stay,
      irony_score: ironyScore,
    };
  });

  async function refSideSummary(side: "leave" | "stay", includeOrgs = false) {
    const rows = await query<{
      org_name: string; org_slug: string; website_url: string; hostname: string;
      hosting_provider: string | null; ip_country: string | null;
      ip_region: string | null; ip_city: string | null;
      sovereignty_tier: number | null; cdn_detected: string | null;
    }>(
      `SELECT o.name AS org_name, o.slug AS org_slug,
              w.url AS website_url, w.hostname,
              s.hosting_provider, s.ip_country, s.ip_region, s.ip_city,
              s.sovereignty_tier, s.cdn_detected
       FROM organizations o
       JOIN websites w ON w.owner_type='organization' AND w.owner_id=o.id AND w.is_active=true
       LEFT JOIN LATERAL (
         SELECT * FROM infrastructure_scans WHERE website_id = w.id
         ORDER BY scanned_at DESC LIMIT 1
       ) s ON true
       WHERE o.side = $1 OR (o.type = 'referendum_leave' AND $1='leave')
                          OR (o.type = 'referendum_stay'  AND $1='stay')
       ORDER BY o.name, w.label`,
      [side]
    );

    const totalWebsites = rows.length;
    const hostedInAlberta = rows.filter(r => r.ip_region === "Alberta").length;
    const hostedInCanada = rows.filter(r => r.ip_country === "CA").length;
    const hostedInUS = rows.filter(r => r.ip_country === "US").length;
    const cdnFronted = rows.filter(r => r.cdn_detected).length;

    const providers = Array.from(
      new Set(rows.map(r => r.hosting_provider).filter((x): x is string => !!x))
    );
    const orgs = Array.from(new Set(rows.map(r => r.org_name)));

    const payload: Record<string, unknown> = {
      orgs,
      total_websites: totalWebsites,
      hosted_in_alberta: hostedInAlberta,
      hosted_in_canada: hostedInCanada,
      hosted_in_us: hostedInUS,
      cdn_fronted: cdnFronted,
      providers,
    };
    if (includeOrgs) payload.websites = rows;
    return payload;
  }

  function buildIronyScore(
    leave: Record<string, unknown>,
    stay: Record<string, unknown>
  ): string {
    const leaveTotal = leave.total_websites as number;
    const leaveCA = leave.hosted_in_canada as number;
    const stayTotal = stay.total_websites as number;
    const stayCA = stay.hosted_in_canada as number;
    if (leaveTotal === 0) return "";

    const leaveOutside = leaveTotal - leaveCA;
    const stayOutside = stayTotal - stayCA;

    if (leaveCA === 0 && stayCA === 0 && stayTotal > 0) {
      return "Neither side of Alberta's sovereignty debate hosts their digital infrastructure in Canada.";
    }
    if (leaveOutside / leaveTotal >= 0.5) {
      const pct = Math.round(100 * leaveOutside / leaveTotal);
      return `Organizations advocating Alberta leave Canada for sovereignty, and those organizing to keep Alberta in Canada, store 100% of their website data outside Alberta.`;
    }
    return "";
  }
}
