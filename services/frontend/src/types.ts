export type SovereigntyTier = 1 | 2 | 3 | 4 | 5 | 6;

export const TIER_META: Record<SovereigntyTier, { label: string; emoji: string; color: string }> = {
  1: { label: "Canadian Sovereign", emoji: "🍁", color: "#dc2626" },
  2: { label: "Canadian Soil",      emoji: "🇨🇦", color: "#ea580c" },
  3: { label: "CDN-Fronted",        emoji: "🌐", color: "#0891b2" },
  4: { label: "US-Hosted",          emoji: "🇺🇸", color: "#6366f1" },
  5: { label: "Other Foreign",      emoji: "🌍", color: "#a855f7" },
  6: { label: "Unknown",            emoji: "❓", color: "#64748b" },
};

export interface StatsResponse {
  politicians: {
    total: number;
    by_level: Record<string, number>;
    by_party: Array<{ party: string; n: number }>;
    sovereignty: Record<string, number>;
    pct_not_canadian: number;
  };
  politicians_by_province?: Record<string, number>;
  socials_adoption?: {
    by_platform: Record<string, number>;
    total_with_any: number;
    total_without: number;
  };
  dead_socials_pct?: number;
  recent_changes_24h?: number;
  organizations: {
    total: number;
    referendum: {
      leave: ReferendumSideSummary;
      stay: ReferendumSideSummary;
    };
  };
  top_server_locations: Array<{ city: string; country: string; n: number }>;
  top_foreign_locations: Array<{ city: string; country: string; n: number }>;
  top_providers: Array<{ provider: string; n: number }>;
}

export const COUNTRY_FLAGS: Record<string, string> = {
  CA: "🇨🇦", US: "🇺🇸", GB: "🇬🇧", FR: "🇫🇷", DE: "🇩🇪",
  NL: "🇳🇱", IE: "🇮🇪", SG: "🇸🇬", JP: "🇯🇵", AU: "🇦🇺",
};

export interface ReferendumSideSummary {
  orgs: string[];
  total_websites: number;
  hosted_in_alberta: number;
  hosted_in_canada: number;
  hosted_in_us: number;
  cdn_fronted: number;
  providers: string[];
  websites?: Array<{
    org_name: string;
    org_slug: string;
    website_url: string;
    hostname: string;
    hosting_provider: string | null;
    ip_country: string | null;
    ip_city: string | null;
    sovereignty_tier: number | null;
    cdn_detected: string | null;
  }>;
}

export interface GeoFeature {
  type: "Feature";
  id?: string;
  properties: Record<string, unknown>;
  geometry: unknown;
}

export interface GeoCollection {
  type: "FeatureCollection";
  features: GeoFeature[];
}

export interface ChangeItem {
  id: string;
  website_id: string;
  detected_at: string;
  change_type: string;
  old_value: string | null;
  new_value: string | null;
  severity: "info" | "notable" | "major";
  summary: string;
  website_url: string;
  owner_type: "politician" | "organization";
  owner_id: string;
  owner_name: string;
}
