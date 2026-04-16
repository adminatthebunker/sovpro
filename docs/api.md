# API Reference

Base URL: `http://<host>/api/v1`

All endpoints return JSON. Pagination: `?page=N&limit=M` where applicable.

## Politicians

### `GET /politicians`
Query params:
- `level` — `federal | provincial | municipal`
- `province` — 2-letter (`AB`, `ON`, ...)
- `party` — exact match
- `sovereignty_tier` — 1-6
- `search` — name substring (ILIKE)
- `page`, `limit` (max 500, default 50)

### `GET /politicians/:id`
Returns the politician, all websites with their latest scan, and the constituency boundary GeoJSON.

## Organizations

### `GET /organizations`
- `type` — `referendum_leave | referendum_stay | political_party | indigenous_rights | advocacy | government_body | media`
- `side` — `leave | stay | neutral`
- `search` — name substring

### `GET /organizations/:idOrSlug`
Looks up by UUID or slug (e.g. `alberta-prosperity-project`).

## Map

### `GET /map/geojson`
- `level`, `province`, `group=politicians|organizations|all`

Returns a `FeatureCollection` containing three feature kinds:
- `kind: "constituency"` — MultiPolygon
- `kind: "server"` — Point
- `kind: "connection"` — LineString from constituency centroid → server

### `GET /map/referendum`
Returns a `FeatureCollection` focused on referendum orgs, with the AB provincial boundary as context.

## Stats

### `GET /stats`
Top-level rollup: politicians by level/party, sovereignty distribution, top providers + locations, organizations summary.

### `GET /stats/referendum`
```json
{
  "leave_side":  { "orgs": [...], "total_websites": N, "hosted_in_us": N, ... },
  "stay_side":   { ... },
  "irony_score": "Organizations advocating to leave Canada..."
}
```

## Coverage

### `GET /coverage`
Query params:
- `status` — filter by `bills_status`: `live | partial | blocked | none`

Returns the `jurisdiction_sources` table plus a rollup summary:

```json
{
  "jurisdictions": [
    {
      "jurisdiction": "AB",
      "legislature_name": "Legislative Assembly of Alberta",
      "seats": 87,
      "bills_status": "live",
      "hansard_status": "none",
      "votes_status": "none",
      "committees_status": "live",
      "bills_difficulty": 2,
      "blockers": null,
      "notes": "Legislature 31 S1+S2 live (114 bills); Hansard is PDF-only",
      "bills_count": 0,
      "speeches_count": 0,
      "votes_count": 0,
      "politicians_count": 0,
      "last_verified_at": null
    }
  ],
  "summary": { "total": 14, "live": 8, "partial": 2, "blocked": 2, "none": 2 }
}
```

Seeded on migration 0019 and kept current by `jurisdiction_sources` updates from ingest pipelines.

## Changes

### `GET /changes`
- `since` (ISO timestamp), `owner_type`, `change_type`, `severity`
- Returns scan deltas with owner name + URL.

## Webhooks

### `POST /webhooks/change`
Receives notifications from the `change` detection container. Authenticated via:
```
X-Signature: sha256=<hex(hmac_sha256(WEBHOOK_SECRET, raw_body))>
```
If `WEBHOOK_SECRET` is unset, the endpoint accepts unsigned posts (dev mode only).

## Open Graph

### `GET /og/share`
Returns a dynamic **1200×630 PNG** share card with the current headline stat
(% of Canadian politicians hosting outside Canada) and a sovereignty-tier bar
chart. Intended for use in `<meta property="og:image">` tags.

- `Content-Type: image/png`
- `Cache-Control: public, max-age=300`
- In-process cache refreshes every 5 minutes from live `/stats` data.

## Health

### `GET /health`
```json
{ "ok": true, "db": true }
```
