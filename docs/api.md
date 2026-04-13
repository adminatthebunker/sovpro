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

## Health

### `GET /health`
```json
{ "ok": true, "db": true }
```
