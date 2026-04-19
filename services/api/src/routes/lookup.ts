import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query } from "../db.js";
import { resolvePhotoUrl } from "../lib/photos.js";

/**
 * /api/v1/lookup/postcode/:code
 *
 * Resolve a Canadian postal code to representatives at all levels via Open
 * North's Represent API, then enrich each rep with our own latest-scan data.
 */
const POSTAL_RE = /^[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d$/;

interface OpenNorthRep {
  name: string;
  district_name?: string;
  elected_office?: string;
  representative_set_name?: string;
  party_name?: string;
  email?: string;
  url?: string;
  personal_url?: string;
  photo_url?: string;
}

interface OpenNorthPostcodeResponse {
  representatives_centroid?: OpenNorthRep[];
  representatives_concordance?: OpenNorthRep[];
  boundaries_centroid?: Array<{ name: string; related?: { boundary_set_name?: string } }>;
}

function normalize(code: string): string {
  return code.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
}

export default async function lookupRoutes(app: FastifyInstance) {
  app.get("/postcode/:code", async (req, reply) => {
    const { code } = req.params as { code: string };
    if (!POSTAL_RE.test(code)) {
      return reply.badRequest("Invalid Canadian postal code (e.g. K1A 0A6)");
    }
    const norm = normalize(code);
    const formatted = `${norm.slice(0, 3)} ${norm.slice(3)}`;

    let upstream: OpenNorthPostcodeResponse;
    try {
      const r = await fetch(
        `https://represent.opennorth.ca/postcodes/${norm}/?format=json`,
        { headers: { "User-Agent": "CanadianPoliticalData/1.0" } }
      );
      if (r.status === 404) return reply.notFound("Postal code not found");
      if (!r.ok) {
        app.log.warn({ status: r.status, code: norm }, "Open North postcode lookup failed");
        return reply.serviceUnavailable("Postal code lookup service is unreachable");
      }
      upstream = (await r.json()) as OpenNorthPostcodeResponse;
    } catch (err) {
      app.log.error(err, "postcode upstream error");
      return reply.serviceUnavailable("Postal code lookup service is unreachable");
    }

    const reps = [
      ...(upstream.representatives_centroid ?? []),
      ...(upstream.representatives_concordance ?? []),
    ];

    // Enrich each rep with our local scan data — match by (name, district)
    const enriched = await Promise.all(reps.map(async (rep) => {
      const local = await query<{
        id: string; name: string; party: string | null;
        elected_office: string | null; level: string;
        constituency_name: string | null;
        photo_path: string | null; photo_url: string | null;
        worst_tier: number | null; best_tier: number | null;
        websites: number; canadian: number; cdn: number; us: number; foreign: number;
      }>(
        `SELECT p.id, p.name, p.party, p.elected_office, p.level, p.constituency_name,
                p.photo_path, p.photo_url,
                MAX(s.sovereignty_tier) AS worst_tier,
                MIN(s.sovereignty_tier) AS best_tier,
                COUNT(DISTINCT w.id) FILTER (WHERE w.label <> 'shared_official')::int AS websites,
                COUNT(*) FILTER (WHERE s.sovereignty_tier IN (1,2))::int AS canadian,
                COUNT(*) FILTER (WHERE s.sovereignty_tier = 3)::int AS cdn,
                COUNT(*) FILTER (WHERE s.sovereignty_tier = 4)::int AS us,
                COUNT(*) FILTER (WHERE s.sovereignty_tier = 5)::int AS foreign
         FROM politicians p
         LEFT JOIN websites w ON w.owner_type='politician' AND w.owner_id=p.id AND w.is_active
                              AND COALESCE(w.label,'') <> 'shared_official'
         LEFT JOIN LATERAL (
           SELECT * FROM infrastructure_scans WHERE website_id = w.id
           ORDER BY scanned_at DESC LIMIT 1
         ) s ON true
         WHERE p.is_active = true
           AND lower(p.name) = lower($1)
           AND ($2::text IS NULL OR lower(p.constituency_name) = lower($2))
         GROUP BY p.id`,
        [rep.name, rep.district_name ?? null]
      );

      const sites = local[0]
        ? await query<{
            url: string; hostname: string; label: string | null;
            tier: number | null; provider: string | null; country: string | null; city: string | null;
          }>(
            `SELECT w.url, w.hostname, w.label,
                    s.sovereignty_tier AS tier, s.hosting_provider AS provider,
                    s.ip_country AS country, s.ip_city AS city
             FROM websites w
             LEFT JOIN LATERAL (SELECT * FROM infrastructure_scans WHERE website_id=w.id
                                 ORDER BY scanned_at DESC LIMIT 1) s ON true
             WHERE w.owner_type='politician' AND w.owner_id=$1 AND w.is_active
               AND COALESCE(w.label,'') <> 'shared_official'
             ORDER BY w.label`, [local[0].id]
          )
        : [];

      return {
        politician_id: local[0]?.id ?? null,
        name: rep.name,
        district: rep.district_name,
        elected_office: rep.elected_office,
        party: rep.party_name,
        email: rep.email,
        photo_url: local[0]
          ? resolvePhotoUrl({ photo_path: local[0].photo_path, photo_url: rep.photo_url ?? local[0].photo_url })
          : (rep.photo_url ?? null),
        in_database: !!local[0],
        scan_summary: local[0]
          ? {
              websites: local[0].websites,
              canadian: local[0].canadian,
              cdn: local[0].cdn,
              us: local[0].us,
              foreign: local[0].foreign,
              worst_tier: local[0].worst_tier,
              best_tier: local[0].best_tier,
            }
          : null,
        sites,
      };
    }));

    reply.header("cache-control", "public, max-age=600");
    return {
      postal_code: formatted,
      representatives: enriched,
    };
  });
}
