import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query, queryOne } from "../db.js";
import { resolvePhotoUrl } from "../lib/photos.js";

/** Collapse whitespace and cap at `max` chars on a word boundary, appending ellipsis. */
function truncateQuote(raw: string, max: number): string {
  const clean = raw.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  const trimmed = (lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut).replace(/[\s,.;:!?—-]+$/, "");
  return `${trimmed}…`;
}

// ── Shared helpers ────────────────────────────────────────────────
//
// Coerces the classic `?has_twitter=true` shape that HTML forms emit.
// `z.coerce.boolean()` is unsafe here because it treats "false" as truthy.
const boolish = z
  .union([z.string(), z.boolean()])
  .transform((v) => {
    if (typeof v === "boolean") return v;
    const s = v.toLowerCase();
    if (s === "true" || s === "1" || s === "yes") return true;
    if (s === "false" || s === "0" || s === "no") return false;
    return undefined;
  })
  .pipe(z.boolean());

const listQuery = z.object({
  level: z.enum(["federal", "provincial", "municipal"]).optional(),
  province: z.string().length(2).optional(),
  party: z.string().optional(),
  sovereignty_tier: z.coerce.number().int().min(1).max(6).optional(),
  search: z.string().optional(),
  // Phase 7a additions
  committee: z.string().optional(),
  office: z.string().optional(),
  has_twitter: boolish.optional(),
  has_facebook: boolish.optional(),
  has_instagram: boolish.optional(),
  has_youtube: boolish.optional(),
  has_tiktok: boolish.optional(),
  has_linkedin: boolish.optional(),
  socials_live: boolish.optional(),
  page: z.coerce.number().int().min(1).default(1),
  limit: z.coerce.number().int().min(1).max(500).default(50),
});

// Politician change-tracking filters (Phase 6).
const changesQuery = z.object({
  change_type: z
    .enum([
      "party_switch",
      "office_change",
      "retired",
      "newly_elected",
      "social_added",
      "social_removed",
      "social_dead",
      "constituency_change",
      "name_change",
    ])
    .optional(),
  level: z.enum(["federal", "provincial", "municipal"]).optional(),
  province: z.string().length(2).optional(),
  severity: z.enum(["info", "notable", "major"]).optional(),
  limit: z.coerce.number().int().min(1).max(500).default(50),
});

// Each (platform -> column name in EXISTS subquery). Used to build
// EXISTS/NOT EXISTS predicates in a data-driven way.
const PLATFORM_FILTERS: Array<{ param: keyof z.infer<typeof listQuery>; platform: string }> = [
  { param: "has_twitter",   platform: "twitter"   },
  { param: "has_facebook",  platform: "facebook"  },
  { param: "has_instagram", platform: "instagram" },
  { param: "has_youtube",   platform: "youtube"   },
  { param: "has_tiktok",    platform: "tiktok"    },
  { param: "has_linkedin",  platform: "linkedin"  },
];

export default async function politicianRoutes(app: FastifyInstance) {
  // Recent politician-level changes (party switches, retirements, etc.).
  // Minimal batched resolver: given N ids (comma-separated, max 20),
  // return `{id, name, photo_url, slug}` for each that exists. Used by
  // the search page to render politician-pin chips when the user arrives
  // via a URL carrying `?politician_id=<uuid>` values — we know the
  // UUIDs from the URL but need names to label the chips.
  // Registered before /:id so the static path wins routing.
  app.get("/resolve", async (req, reply) => {
    const raw = (req.query as { ids?: string }).ids ?? "";
    const ids = raw.split(",").map(s => s.trim()).filter(Boolean);
    if (ids.length === 0) return { items: [] };
    if (ids.length > 20) return reply.badRequest("max 20 ids per request");
    const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!ids.every(id => UUID.test(id))) return reply.badRequest("invalid uuid");
    const rows = await query<{
      id: string;
      name: string;
      photo_url: string | null;
      photo_path: string | null;
      openparliament_slug: string | null;
    }>(
      `SELECT id, name, photo_url, photo_path, openparliament_slug
         FROM politicians WHERE id = ANY($1::uuid[])`,
      [ids],
    );
    return {
      items: rows.map(r => ({
        id: r.id,
        name: r.name,
        photo_url: resolvePhotoUrl(r),
        slug: r.openparliament_slug,
      })),
    };
  });

  // Lightweight typeahead for the pin picker: name-prefix match, tiny
  // projection, capped at 10 rows. Distinct from `/` (full listing w/
  // socials/offices joins — heavy, wrong shape for a dropdown).
  app.get("/search", async (req, reply) => {
    const q = ((req.query as { q?: string }).q ?? "").trim();
    if (q.length < 2) return { items: [] };
    if (q.length > 64) return reply.badRequest("q too long");
    const rows = await query<{
      id: string;
      name: string;
      photo_url: string | null;
      photo_path: string | null;
      openparliament_slug: string | null;
      party: string | null;
      level: string | null;
      province_territory: string | null;
    }>(
      `SELECT id, name, photo_url, photo_path, openparliament_slug,
              party, level, province_territory
         FROM politicians
        WHERE is_active = true
          AND name ILIKE $1
        ORDER BY name
        LIMIT 10`,
      [`%${q}%`],
    );
    return {
      items: rows.map(r => ({
        id: r.id,
        name: r.name,
        photo_url: resolvePhotoUrl(r),
        slug: r.openparliament_slug,
        party: r.party,
        level: r.level,
        province_territory: r.province_territory,
      })),
    };
  });

  // Registered before /:id so the static path wins routing.
  app.get("/changes", async (req, reply) => {
    const q = changesQuery.safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);
    const { change_type, level, province, severity, limit } = q.data;

    const where: string[] = [];
    const params: (string | number)[] = [];
    if (change_type) { params.push(change_type); where.push(`c.change_type = $${params.length}`); }
    if (level)       { params.push(level);       where.push(`p.level = $${params.length}`); }
    if (province)    { params.push(province);    where.push(`p.province_territory = $${params.length}`); }
    if (severity)    { params.push(severity);    where.push(`c.severity = $${params.length}`); }
    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

    const items = await query(
      `
      SELECT c.id, c.politician_id, c.change_type, c.old_value, c.new_value,
             c.severity, c.detected_at,
             p.name AS politician_name, p.level, p.province_territory,
             p.party, p.elected_office, p.constituency_name, p.is_active
        FROM politician_changes c
        JOIN politicians p ON p.id = c.politician_id
        ${whereSql}
       ORDER BY c.detected_at DESC
       LIMIT ${limit}
      `,
      params,
    );
    return { items, limit };
  });

  // Change history for a single politician.
  app.get("/:id/changes", async (req, reply) => {
    const { id } = req.params as { id: string };
    const pol = await queryOne(
      `SELECT id, name FROM politicians WHERE id = $1`,
      [id],
    );
    if (!pol) return reply.notFound();

    const changes = await query(
      `
      SELECT id, politician_id, change_type, old_value, new_value,
             severity, detected_at
        FROM politician_changes
       WHERE politician_id = $1
       ORDER BY detected_at DESC
       LIMIT 200
      `,
      [id],
    );
    return { politician: pol, items: changes };
  });

  // Term history for a single politician.
  app.get("/:id/terms", async (req, reply) => {
    const { id } = req.params as { id: string };
    const pol = await queryOne(
      `SELECT id, name FROM politicians WHERE id = $1`,
      [id],
    );
    if (!pol) return reply.notFound();

    const terms = await query(
      `
      SELECT id, politician_id, office, party, level, province_territory,
             constituency_id, started_at, ended_at, source, created_at
        FROM politician_terms
       WHERE politician_id = $1
       ORDER BY started_at DESC, created_at DESC
      `,
      [id],
    );
    return { politician: pol, terms };
  });

  // Constituency / legislature offices for a single politician (Phase 7a).
  app.get("/:id/offices", async (req, reply) => {
    const { id } = req.params as { id: string };
    const pol = await queryOne(
      `SELECT id, name FROM politicians WHERE id = $1`,
      [id],
    );
    if (!pol) return reply.notFound();

    const offices = await query(
      `
      SELECT id, politician_id, kind, address, city, province_territory,
             postal_code, phone, fax, email, hours, lat, lon,
             source, created_at, updated_at
        FROM politician_offices
       WHERE politician_id = $1
       ORDER BY kind NULLS LAST, city NULLS LAST
      `,
      [id],
    );
    return { politician: pol, offices };
  });

  // Committee memberships for a single politician (Phase 7a).
  app.get("/:id/committees", async (req, reply) => {
    const { id } = req.params as { id: string };
    const pol = await queryOne(
      `SELECT id, name FROM politicians WHERE id = $1`,
      [id],
    );
    if (!pol) return reply.notFound();

    const committees = await query(
      `
      SELECT id, politician_id, committee_name, role, level,
             started_at, ended_at, source, created_at
        FROM politician_committees
       WHERE politician_id = $1
       ORDER BY ended_at NULLS FIRST, committee_name
      `,
      [id],
    );
    return { politician: pol, committees };
  });

  app.get("/", async (req, reply) => {
    const q = listQuery.safeParse(req.query);
    if (!q.success) return reply.badRequest(q.error.message);
    const {
      level, province, party, sovereignty_tier, search,
      committee, office,
      has_twitter, has_facebook, has_instagram,
      has_youtube, has_tiktok, has_linkedin,
      socials_live,
      page, limit,
    } = q.data;
    const offset = (page - 1) * limit;

    const where: string[] = ["p.is_active = true"];
    const params: (string | number)[] = [];
    if (level)    { params.push(level);    where.push(`p.level = $${params.length}`); }
    if (province) { params.push(province); where.push(`p.province_territory = $${params.length}`); }
    if (party)    { params.push(party);    where.push(`p.party = $${params.length}`); }
    if (search)   { params.push(`%${search}%`); where.push(`p.name ILIKE $${params.length}`); }
    if (office)   { params.push(`%${office}%`); where.push(`p.elected_office ILIKE $${params.length}`); }

    if (committee) {
      params.push(`%${committee}%`);
      where.push(
        `EXISTS (SELECT 1 FROM politician_committees pc
                  WHERE pc.politician_id = p.id
                    AND pc.committee_name ILIKE $${params.length})`
      );
    }

    // Per-platform has_<platform> filters.
    const hasFlags: Record<string, boolean | undefined> = {
      has_twitter, has_facebook, has_instagram,
      has_youtube, has_tiktok, has_linkedin,
    };
    for (const { param, platform } of PLATFORM_FILTERS) {
      const v = hasFlags[param as string];
      if (v === undefined) continue;
      params.push(platform);
      const predicate =
        `(SELECT 1 FROM politician_socials ps
           WHERE ps.politician_id = p.id AND ps.platform = $${params.length})`;
      where.push(v ? `EXISTS ${predicate}` : `NOT EXISTS ${predicate}`);
    }

    // socials_live=true: has at least one live handle.
    // socials_live=false: zero live handles (dead-only or no-verification).
    if (socials_live !== undefined) {
      const predicate =
        `(SELECT 1 FROM politician_socials ps
           WHERE ps.politician_id = p.id AND ps.is_live = true)`;
      where.push(socials_live ? `EXISTS ${predicate}` : `NOT EXISTS ${predicate}`);
    }

    // sovereignty_tier joins latest scan across any of their websites
    let tierJoin = "";
    if (sovereignty_tier) {
      params.push(sovereignty_tier);
      tierJoin = `
        JOIN LATERAL (
            SELECT 1 FROM websites w
            JOIN LATERAL (SELECT * FROM infrastructure_scans WHERE website_id = w.id
                          ORDER BY scanned_at DESC LIMIT 1) s ON true
            WHERE w.owner_type='politician' AND w.owner_id = p.id
              AND s.sovereignty_tier = $${params.length}
            LIMIT 1
        ) sv ON true
      `;
    }

    const countSql = `SELECT COUNT(*)::int AS n FROM politicians p ${tierJoin} WHERE ${where.join(" AND ")}`;
    const total = (await queryOne<{ n: number }>(countSql, params))?.n ?? 0;

    // Aggregate fields: platforms array + office/committee counts +
    // current-term start timestamp. All joined via LEFT JOIN LATERAL
    // so the row is returned even when no related rows exist.
    //
    // `latest_speech` is a scalar subquery (not LATERAL) so Postgres only
    // evaluates it for the 50-ish rows that survive LIMIT; without NULLS LAST
    // the planner can walk idx_speeches_politician backwards and stop at the
    // first row with word_count >= 15 — sub-millisecond per politician.
    const listSql = `
      SELECT
        p.*,
        (SELECT COUNT(*) FROM websites w
           WHERE w.owner_type='politician' AND w.owner_id=p.id AND w.is_active)::int
          AS website_count,
        COALESCE(socials.platforms, '{}'::text[]) AS social_platforms,
        COALESCE(offices.n, 0)::int               AS office_count,
        COALESCE(committees.n, 0)::int            AS committee_count,
        term.started_at                           AS current_term_started_at,
        (SELECT jsonb_build_object('text', text, 'spoken_at', spoken_at)
           FROM speeches
          WHERE politician_id = p.id AND word_count >= 15
          ORDER BY spoken_at DESC
          LIMIT 1)                                AS latest_speech
      FROM politicians p
      ${tierJoin}
      LEFT JOIN LATERAL (
        SELECT array_agg(DISTINCT platform ORDER BY platform) AS platforms
          FROM politician_socials
         WHERE politician_id = p.id
      ) socials ON true
      LEFT JOIN LATERAL (
        SELECT COUNT(*) AS n FROM politician_offices WHERE politician_id = p.id
      ) offices ON true
      LEFT JOIN LATERAL (
        SELECT COUNT(*) AS n FROM politician_committees WHERE politician_id = p.id
      ) committees ON true
      LEFT JOIN LATERAL (
        SELECT started_at FROM politician_terms
         WHERE politician_id = p.id AND ended_at IS NULL
         ORDER BY started_at DESC
         LIMIT 1
      ) term ON true
      WHERE ${where.join(" AND ")}
      ORDER BY p.last_name NULLS LAST, p.name
      LIMIT ${limit} OFFSET ${offset}
    `;
    const rawItems = await query<Record<string, unknown>>(listSql, params);
    const items = rawItems.map((row) => {
      const speech = row.latest_speech as { text?: string; spoken_at?: string } | null;
      const excerpt = speech?.text ? truncateQuote(speech.text, 240) : null;
      return {
        ...row,
        photo_url: resolvePhotoUrl(row as { photo_path?: string | null; photo_url?: string | null }),
        latest_speech_text: excerpt,
        latest_speech_at: speech?.spoken_at ?? null,
        latest_speech: undefined,
      };
    });

    return { items, page, limit, total, pages: Math.max(1, Math.ceil(total / limit)) };
  });

  app.get("/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    const pol = await queryOne<Record<string, unknown>>(
      `SELECT * FROM politicians WHERE id = $1 AND is_active = true`, [id]
    );
    if (!pol) return reply.notFound();
    (pol as Record<string, unknown>).photo_url = resolvePhotoUrl(
      pol as { photo_path?: string | null; photo_url?: string | null }
    );

    const websites = await query(
      `
      SELECT w.*, s.ip_country, s.ip_city, s.ip_latitude, s.ip_longitude,
             s.hosting_provider, s.hosting_country, s.sovereignty_tier,
             s.cdn_detected, s.cms_detected, s.scanned_at
      FROM websites w
      LEFT JOIN LATERAL (
        SELECT * FROM infrastructure_scans WHERE website_id = w.id
        ORDER BY scanned_at DESC LIMIT 1
      ) s ON true
      WHERE w.owner_type='politician' AND w.owner_id=$1 AND w.is_active=true
      ORDER BY w.label
      `, [id]
    );

    const boundary = (pol as { constituency_id?: string }).constituency_id
      ? await queryOne(
          `SELECT constituency_id, name, level, ST_AsGeoJSON(boundary_simple)::jsonb AS boundary_geojson,
                  ST_X(centroid) AS centroid_lng, ST_Y(centroid) AS centroid_lat
           FROM constituency_boundaries WHERE constituency_id = $1`,
           [(pol as { constituency_id: string }).constituency_id])
      : null;

    return { politician: pol, websites, boundary };
  });
}
