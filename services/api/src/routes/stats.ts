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

    // Sovereignty tier distribution for politicians (latest scan per website)
    const polTiers = await query<{ tier: number; n: number }>(
      `SELECT sovereignty_tier AS tier, COUNT(*)::int AS n
       FROM map_politicians WHERE sovereignty_tier IS NOT NULL
       GROUP BY sovereignty_tier ORDER BY sovereignty_tier`);

    // Percent not Canadian (tiers 3-5)
    const notCanadian = await query<{ pct: number }>(
      `SELECT COALESCE(
         100.0 * SUM(CASE WHEN sovereignty_tier IN (3,4,5) THEN 1 ELSE 0 END)
               / NULLIF(COUNT(*),0),
         0)::float AS pct
       FROM map_politicians WHERE sovereignty_tier IS NOT NULL`);

    // Top cities / providers (all scanned websites)
    const topCities = await query<{ city: string; country: string; n: number }>(
      `SELECT ip_city AS city, ip_country AS country, COUNT(*)::int AS n
       FROM (
         SELECT DISTINCT ON (website_id) ip_city, ip_country
         FROM infrastructure_scans ORDER BY website_id, scanned_at DESC
       ) t
       WHERE ip_city IS NOT NULL GROUP BY ip_city, ip_country ORDER BY n DESC LIMIT 10`);
    const topProviders = await query<{ provider: string; n: number }>(
      `SELECT hosting_provider AS provider, COUNT(*)::int AS n
       FROM (
         SELECT DISTINCT ON (website_id) hosting_provider
         FROM infrastructure_scans ORDER BY website_id, scanned_at DESC
       ) t
       WHERE hosting_provider IS NOT NULL GROUP BY hosting_provider ORDER BY n DESC LIMIT 10`);

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
        sovereignty: Object.fromEntries(polTiers.map(r => [`tier_${r.tier}`, r.n])),
        pct_not_canadian: Math.round((notCanadian[0]?.pct ?? 0) * 10) / 10,
      },
      organizations: {
        total: orgTotal?.n ?? 0,
        referendum: { leave: refLeave, stay: refStay },
      },
      top_server_locations: topCities,
      top_providers: topProviders,
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
      hosting_provider: string | null; ip_country: string | null; ip_city: string | null;
      sovereignty_tier: number | null; cdn_detected: string | null;
    }>(
      `SELECT o.name AS org_name, o.slug AS org_slug,
              w.url AS website_url, w.hostname,
              s.hosting_provider, s.ip_country, s.ip_city,
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
      return `Organizations advocating Alberta leave Canada for sovereignty store ${pct}% of their website data outside Canada.`;
    }
    return "";
  }
}
