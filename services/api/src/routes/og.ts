import type { FastifyInstance } from "fastify";
import { Resvg } from "@resvg/resvg-js";
import { query } from "../db.js";

type TierCounts = Record<string, number>;

interface CachedImage {
  png: Buffer;
  at: number;
}

const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
let cache: CachedImage | null = null;

async function gatherStats(): Promise<{
  pctNotCanadian: number;
  tiers: TierCounts;
  totalPoliticians: number;
  uniqueHostnames: number;
}> {
  const totalRow = (await query<{ total: number }>(
    `SELECT COUNT(*)::int AS total FROM politicians WHERE is_active=true`
  ))[0];

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
     FROM uniq GROUP BY sovereignty_tier ORDER BY sovereignty_tier`
  );

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
     FROM uniq`
  );

  const tiers: TierCounts = {};
  let uniqueHostnames = 0;
  for (const r of polTiers) {
    tiers[`tier_${r.tier}`] = r.n;
    uniqueHostnames += r.n;
  }

  return {
    pctNotCanadian: Math.round((notCanadian[0]?.pct ?? 0) * 10) / 10,
    tiers,
    totalPoliticians: totalRow?.total ?? 0,
    uniqueHostnames,
  };
}

function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function buildSvg(stats: Awaited<ReturnType<typeof gatherStats>>): string {
  const { pctNotCanadian, tiers, totalPoliticians } = stats;
  const pctDisplay = Math.round(pctNotCanadian);

  // Sovereignty tier bar chart — 5 tiers
  const tierLabels = [
    { key: "tier_1", label: "T1 Canadian", color: "#22c55e" },
    { key: "tier_2", label: "T2 Canadian-adj.", color: "#84cc16" },
    { key: "tier_3", label: "T3 Foreign", color: "#f59e0b" },
    { key: "tier_4", label: "T4 US hyperscaler", color: "#f97316" },
    { key: "tier_5", label: "T5 High-risk", color: "#e11d48" },
  ];
  const counts = tierLabels.map((t) => tiers[t.key] ?? 0);
  const maxCount = Math.max(1, ...counts);

  const chartX = 80;
  const chartY = 400;
  const chartW = 1040;
  const chartH = 110;
  const gap = 24;
  const barW = (chartW - gap * (tierLabels.length - 1)) / tierLabels.length;

  const bars = tierLabels
    .map((t, i) => {
      const c = counts[i] ?? 0;
      const h = Math.round((c / maxCount) * chartH);
      const x = chartX + i * (barW + gap);
      const y = chartY + (chartH - h);
      return `
        <rect x="${x}" y="${y}" width="${barW}" height="${h}" rx="6" fill="${t.color}" opacity="0.9"/>
        <text x="${x + barW / 2}" y="${y - 10}" text-anchor="middle" fill="#e2e8f0" font-family="Inter,Arial,sans-serif" font-size="22" font-weight="600">${c}</text>
        <text x="${x + barW / 2}" y="${chartY + chartH + 30}" text-anchor="middle" fill="#94a3b8" font-family="Inter,Arial,sans-serif" font-size="18">${escapeXml(t.label)}</text>
      `;
    })
    .join("");

  // Decorative Toronto -> Kansas City arc
  const torontoX = 820;
  const torontoY = 170;
  const kcX = 1090;
  const kcY = 260;
  const midX = (torontoX + kcX) / 2;
  const midY = Math.min(torontoY, kcY) - 50;

  const headline = `${pctDisplay}% of Canadian politicians`;
  const subHeadline = `host their websites outside Canada`;

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#020617"/>
      <stop offset="100%" stop-color="#0b1220"/>
    </linearGradient>
    <linearGradient id="flow" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#e11d48" stop-opacity="0.1"/>
      <stop offset="100%" stop-color="#e11d48" stop-opacity="0.8"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)"/>

  <!-- Subtle grid -->
  <g stroke="#1e293b" stroke-width="1" opacity="0.4">
    <line x1="0" y1="100" x2="1200" y2="100"/>
    <line x1="0" y1="380" x2="1200" y2="380"/>
    <line x1="0" y1="560" x2="1200" y2="560"/>
  </g>

  <!-- Wordmark -->
  <g transform="translate(80, 70)">
    <text x="0" y="0" font-family="Inter,Arial,sans-serif" font-size="30" font-weight="700" fill="#e2e8f0">
      <tspan fill="#e11d48">🍁</tspan>
      <tspan dx="12">Canadian Political Data</tspan>
    </text>
  </g>

  <!-- Decorative Toronto -> Kansas City arc -->
  <g opacity="0.85">
    <path d="M ${torontoX} ${torontoY} Q ${midX} ${midY} ${kcX} ${kcY}"
          fill="none" stroke="url(#flow)" stroke-width="3" stroke-dasharray="4 6"/>
    <circle cx="${torontoX}" cy="${torontoY}" r="6" fill="#22c55e"/>
    <text x="${torontoX + 12}" y="${torontoY - 6}" font-family="Inter,Arial,sans-serif" font-size="16" fill="#94a3b8">Toronto</text>
    <circle cx="${kcX}" cy="${kcY}" r="6" fill="#e11d48"/>
    <text x="${kcX - 10}" y="${kcY + 26}" text-anchor="end" font-family="Inter,Arial,sans-serif" font-size="16" fill="#94a3b8">Kansas City</text>
  </g>

  <!-- Headline -->
  <g transform="translate(80, 200)">
    <text x="0" y="0" font-family="Inter,Arial,sans-serif" font-size="84" font-weight="800" fill="#e2e8f0">
      <tspan fill="#e11d48">${pctDisplay}%</tspan><tspan dx="20" fill="#e2e8f0">of Canadian</tspan>
    </text>
    <text x="0" y="84" font-family="Inter,Arial,sans-serif" font-size="56" font-weight="700" fill="#e2e8f0">politicians host websites</text>
    <text x="0" y="144" font-family="Inter,Arial,sans-serif" font-size="56" font-weight="700" fill="#94a3b8">
      <tspan>outside </tspan><tspan fill="#e11d48">Canada</tspan>.
    </text>
  </g>

  <!-- Bar chart -->
  ${bars}

  <!-- Footer -->
  <g transform="translate(80, 588)">
    <text x="0" y="0" font-family="Inter,Arial,sans-serif" font-size="20" font-weight="600" fill="#e2e8f0">canadianpoliticaldata.ca</text>
    <text x="220" y="0" font-family="Inter,Arial,sans-serif" font-size="20" fill="#94a3b8">· scanned ${totalPoliticians} politicians</text>
  </g>
  <!-- suppress unused refs -->
  <!-- headline=${escapeXml(headline)} sub=${escapeXml(subHeadline)} -->
</svg>`;
}

async function renderPng(): Promise<Buffer> {
  const stats = await gatherStats();
  const svg = buildSvg(stats);
  const resvg = new Resvg(svg, {
    background: "#020617",
    fitTo: { mode: "width", value: 1200 },
    font: { loadSystemFonts: true },
  });
  return resvg.render().asPng();
}

export default async function ogRoutes(app: FastifyInstance) {
  app.get("/share", async (_req, reply) => {
    const now = Date.now();
    if (!cache || now - cache.at > CACHE_TTL_MS) {
      try {
        const png = await renderPng();
        cache = { png, at: now };
      } catch (err) {
        app.log.error({ err }, "failed to render OG image");
        if (!cache) {
          reply.code(500);
          return { error: "render_failed" };
        }
      }
    }
    reply
      .header("Content-Type", "image/png")
      .header("Cache-Control", "public, max-age=300")
      .header("Content-Length", cache!.png.length.toString());
    return reply.send(cache!.png);
  });
}
