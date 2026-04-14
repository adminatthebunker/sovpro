export type SovereigntyTier = 1 | 2 | 3 | 4 | 5 | 6;

export interface Politician {
  id: string;
  source_id: string | null;
  name: string;
  first_name: string | null;
  last_name: string | null;
  party: string | null;
  elected_office: string | null;
  level: "federal" | "provincial" | "municipal";
  province_territory: string | null;
  constituency_name: string | null;
  constituency_id: string | null;
  email: string | null;
  photo_url: string | null;
  personal_url: string | null;
  official_url: string | null;
  social_urls: Record<string, string>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Organization {
  id: string;
  slug: string;
  name: string;
  type: string;
  side: "leave" | "stay" | "neutral" | null;
  description: string | null;
  key_people: Array<{ name: string; role?: string }>;
  province_territory: string | null;
  social_urls: Record<string, string>;
  is_active: boolean;
}

export interface Website {
  id: string;
  owner_type: "politician" | "organization";
  owner_id: string;
  url: string;
  hostname: string;
  label: string | null;
  is_active: boolean;
  last_scanned_at: string | null;
  last_changed_at: string | null;
}

export interface Scan {
  id: string;
  website_id: string;
  scanned_at: string;
  ip_country: string | null;
  ip_city: string | null;
  ip_latitude: number | null;
  ip_longitude: number | null;
  hosting_provider: string | null;
  hosting_country: string | null;
  sovereignty_tier: SovereigntyTier;
  cdn_detected: string | null;
  cms_detected: string | null;
}

export interface PoliticianTerm {
  id: string;
  politician_id: string;
  office: string;
  party: string | null;
  level: string;
  province_territory: string | null;
  constituency_id: string | null;
  started_at: string;
  ended_at: string | null;
  source: string | null;
  created_at: string;
}

export type PoliticianChangeType =
  | "party_switch"
  | "office_change"
  | "retired"
  | "newly_elected"
  | "social_added"
  | "social_removed"
  | "social_dead"
  | "constituency_change"
  | "name_change";

export interface PoliticianChange {
  id: string;
  politician_id: string;
  change_type: PoliticianChangeType;
  old_value: unknown | null;
  new_value: unknown | null;
  severity: string;
  detected_at: string;
}

export interface PoliticianOffice {
  id: string;
  politician_id: string;
  kind: string | null;
  address: string | null;
  city: string | null;
  province_territory: string | null;
  postal_code: string | null;
  phone: string | null;
  fax: string | null;
  email: string | null;
  hours: string | null;
  lat: number | null;
  lon: number | null;
  source: string | null;
  created_at: string;
  updated_at: string;
}

export type SocialPlatform =
  | "twitter"
  | "facebook"
  | "instagram"
  | "youtube"
  | "tiktok"
  | "linkedin"
  | "mastodon"
  | "bluesky"
  | "threads"
  | "other";

export interface PoliticianSocial {
  id: string;
  politician_id: string;
  platform: SocialPlatform;
  handle: string | null;
  url: string;
  last_verified_at: string | null;
  is_live: boolean | null;
  follower_count: number | null;
  created_at: string;
  updated_at: string;
}

export interface PoliticianCommittee {
  id: string;
  politician_id: string;
  committee_name: string;
  role: string | null;
  level: string | null;
  started_at: string | null;
  ended_at: string | null;
  source: string | null;
  created_at: string;
}

export interface MapRow {
  politician_id?: string;
  organization_id?: string;
  name: string;
  party?: string | null;
  elected_office?: string | null;
  photo_url?: string | null;
  level?: string;
  side?: string | null;
  type?: string;
  province_territory: string | null;
  constituency_name?: string | null;
  constituency_id?: string | null;
  boundary_geojson?: unknown;
  constituency_lat?: number | null;
  constituency_lng?: number | null;
  website_id: string;
  website_url: string;
  website_label: string | null;
  hostname: string;
  site_class?: "personal" | "party_managed" | "shared_official";
  ip_country: string | null;
  ip_city: string | null;
  server_lat: number | null;
  server_lng: number | null;
  hosting_provider: string | null;
  hosting_country: string | null;
  sovereignty_tier: SovereigntyTier | null;
  cdn_detected: string | null;
  cms_detected: string | null;
  scanned_at: string | null;
}
