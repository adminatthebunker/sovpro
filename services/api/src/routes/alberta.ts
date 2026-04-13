import type { FastifyInstance } from "fastify";
import { query, queryOne } from "../db.js";

/**
 * /api/v1/alberta/overview — single-call summary for the Alberta Referendum tab.
 *
 * Returns:
 *  - coverage: counts of tracked AB politicians by level (provincial / municipal)
 *    plus a breakdown by representative_set_name (Edmonton, Calgary, Lethbridge, etc.)
 *  - sites: aggregate where AB politicians' personal sites live
 *    (alberta / elsewhere_canada / cdn / us / foreign, plus dedup'd hostname total)
 *  - city_infrastructure: each AB municipality's official site (the ones we
 *    flagged shared_official) with its hosting provider, country, and city
 */
export default async function albertaRoutes(app: FastifyInstance) {
  app.get("/overview", async () => {
    const coverage = await query<{
      level: string;
      set_name: string | null;
      politicians: number;
      with_site: number;
    }>(
      `SELECT p.level,
              p.extras->>'representative_set_name' AS set_name,
              COUNT(*)::int AS politicians,
              COUNT(*) FILTER (WHERE p.personal_url IS NOT NULL AND p.personal_url <> '')::int AS with_site
       FROM politicians p
       WHERE p.is_active = true AND p.province_territory = 'AB'
       GROUP BY p.level, p.extras->>'representative_set_name'
       ORDER BY p.level, set_name`
    );

    const sites = await queryOne<{
      total: number; alberta: number; elsewhere_canada: number;
      cdn: number; us: number; foreign: number; unknown: number;
    }>(
      `WITH per_site AS (
         SELECT DISTINCT ON (mp.hostname)
                mp.hostname, mp.ip_country, mp.ip_region, mp.sovereignty_tier
         FROM map_politicians mp
         WHERE mp.province_territory = 'AB'
         ORDER BY mp.hostname, mp.scanned_at DESC NULLS LAST
       )
       SELECT
         COUNT(*)::int AS total,
         COUNT(*) FILTER (WHERE ip_region = 'Alberta')::int AS alberta,
         COUNT(*) FILTER (WHERE ip_country = 'CA' AND COALESCE(ip_region,'') <> 'Alberta')::int AS elsewhere_canada,
         COUNT(*) FILTER (WHERE sovereignty_tier = 3)::int AS cdn,
         COUNT(*) FILTER (WHERE sovereignty_tier = 4)::int AS us,
         COUNT(*) FILTER (WHERE sovereignty_tier = 5)::int AS foreign,
         COUNT(*) FILTER (WHERE sovereignty_tier = 6 OR sovereignty_tier IS NULL)::int AS unknown
       FROM per_site`
    );

    // City / institutional infrastructure (shared_official sites for AB)
    const cityInfra = await query<{
      hostname: string; covers: number;
      ip_country: string | null; ip_region: string | null; ip_city: string | null;
      hosting_provider: string | null; sovereignty_tier: number | null;
      cdn_detected: string | null;
    }>(
      `SELECT w.hostname,
              COUNT(DISTINCT p.id)::int AS covers,
              MAX(s.ip_country) AS ip_country,
              MAX(s.ip_region) AS ip_region,
              MAX(s.ip_city) AS ip_city,
              MAX(s.hosting_provider) AS hosting_provider,
              MAX(s.sovereignty_tier) AS sovereignty_tier,
              MAX(s.cdn_detected) AS cdn_detected
       FROM websites w
       JOIN politicians p ON p.id = w.owner_id AND w.owner_type = 'politician'
       LEFT JOIN LATERAL (SELECT * FROM infrastructure_scans WHERE website_id = w.id
                          ORDER BY scanned_at DESC LIMIT 1) s ON true
       WHERE p.province_territory = 'AB' AND w.label = 'shared_official'
       GROUP BY w.hostname
       ORDER BY covers DESC`
    );

    return {
      coverage,
      sites,
      city_infrastructure: cityInfra,
    };
  });
}
