# Sovereignty: runtime dependencies on external systems

Canonical tracker for every **runtime** external dependency the public
site and API carry. "Runtime" = something the user's browser or the
API hits at request time — not an ingest-time source. Ingest-time
dependencies on legislatures are expected and tracked elsewhere (see
`docs/research/` and the `jurisdiction_sources` table).

The project's sovereignty framing is infrastructure-centric (see
`docs/goals.md` + the 5-tier classification in
`services/scanner/src/classify.py`). CDNs and foreign-controlled
infra reduce sovereignty; hosting bytes on our own disk increases it.
Bringing upstream content in-house at ingest time is the simplest
form of that for everything that isn't itself an interactive service.

## Status

| # | Dependency | Status | Next step |
|---|---|---|---|
| 1 | Politician portraits (`politicians.photo_url` pointing at openparliament.ca, represent.opennorth.ca, sencanada.ca, provincial legislature sites) | **Done — 2026-04-19.** Migration `0026_politician_photo_local.sql` + scanner `backfill-politician-photos` command mirror bytes onto the `assets` volume; API prefers `photo_path` over `photo_url`. First full run: 2,278 of 2,609 upstream-URL rows mirrored (87.3%) in 19 min 13 s. Remaining 331 failures are upstream data-quality issues (missing protocol prefix or upstream 404) that will resolve on the next roster re-ingest. Daily schedule `0 3 * * *` (`scanner_schedules` id `10cc5062-553e-48be-ab90-da5142740a66`) keeps new additions fresh with 30-day staleness. | — |
| 2 | Leaflet marker icons (previously `unpkg.com/leaflet@1.9.4/dist/images/*`) | **Done — 2026-04-19.** 3 PNGs vendored into `services/frontend/public/leaflet/`; `MapView.tsx` references local paths; frontend image rebuilt and deployed. | — |
| 3 | Map tiles: CARTO (`basemaps.cartocdn.com`) dark theme + OpenStreetMap (`tile.openstreetmap.org`) light theme | Deferred | Dedicated plan. Options: (a) raster-tile cache via nginx + OpenStreetMap tilejson URL rewriting; (b) full self-host with PMTiles + MapLibre GL JS — ~25 GB PMTiles for Canada at Z0–Z14, single static file, good CDN story; (c) switch to vector tiles served by our own OpenMapTiles container — heaviest. Pre-decision: audit `MapView.tsx` consumers to confirm raster is actually required, then size (b). |
| 4 | `api.openparliament.ca` live calls at request time (`services/api/src/routes/openparliament.ts`) | Deferred | Move to a scheduled scanner command `refresh-openparliament-federal`. Persist results in a dedicated `openparliament_cache` table (or extend `politicians` / `politician_activity`). Make the API route DB-only. The existing 30-day stale-cache fallback already mitigates outage risk, so this is a data-quality improvement rather than a sovereignty emergency. |
| 5 | Party / referendum-org logos | **Done — 2026-04-19 (closed: not a dependency).** Audit confirmed: `organizations` table has no logo/image/photo column, `db/seed.sql` has no logo fields, and the frontend has zero `<img>` tags referencing org URLs. Nothing is stored, nothing is rendered. Reopen if a future UI surface starts rendering org logos. | — |
| 6 | Bill PDFs where any jurisdiction links out instead of mirroring | **Done — 2026-04-19 (closed: not a dependency).** Audit confirmed: 0 of 18,782 `bills.source_url` rows point at a `.pdf`; all 9 provincial pipelines use HTML source pages (which are already mirrored into `bills.raw_html` per convention #3). The frontend has no bill detail page yet, so there's no render-time link-out either. Reopen when a bill detail UI lands and we decide whether to surface original source URLs or mirrored content. For jurisdictions whose authoritative format is PDF (e.g. AB Hansard), see the separate AB Hansard pipeline — not a sovereignty issue at the bills layer. | — |

Close an item by changing its status to **Done** and appending a short note with the date and commit hash. Don't delete rows — the history is the audit trail.

## Storage pattern (established in Phase A)

Binary assets live on a named Docker volume called `assets`, mounted:

- **RW** into `scanner` and `scanner-jobs` (writers).
- **RO** into `nginx` at `/var/www/assets` (readers), served under `/assets/*`.

Directory convention:

```
/assets/
  politicians/<uuid>.<ext>     # Phase A
  leaflet/                     # (lives in frontend/public/ — versioned with leaflet pkg)
  org-logos/<slug>.<ext>       # future
  bills/<level>/<id>.pdf       # future
```

**nginx routing caveat.** Vite's build output also lives under
`/assets/*` (hashed bundle files), so the public nginx location can't
be a broad `/assets/` alias — it would shadow the frontend's own
chunks. Instead, each in-house asset category gets its own scoped
location block under `/assets/<category>/`. The `politicians/` one is
in `nginx/conf.d/default.conf`; `org-logos/` and `bills/` will need
their own blocks when those categories land. Anything under `/assets/`
that doesn't match a scoped location falls through to the frontend.

Rules that fall out of this:

- **Store the upstream URL alongside the local path.** Pattern from
  `politicians.photo_path` + `photo_url` + `photo_source_url`. The
  upstream URL is attribution + a re-fetch source; never dereference
  it in user-facing code after local mirror exists.
- **Hash the bytes.** `sha256` hex lets re-runs skip rewrites and
  detect upstream changes without comparing file modification times
  across container restarts.
- **Atomic writes.** Write to `<path>.part`, `os.replace()` into
  final name. nginx never serves a half-written file.
- **Mirror at ingest, not on-demand.** Request-time fetch defeats
  the entire point — upstream outage = local outage.

## Rule for new dependencies

> Before adding any new external URL that gets dereferenced at request
> time (either by the API or by HTML emitted to the browser), check
> this doc. Prefer an ingest-time mirror onto the `assets` volume over
> a live link. If a live link is the only viable option, add a row
> here explaining why.

This is a softer version of CLAUDE.md convention #3 ("store `raw_html`
alongside parsed fields") generalized past text: anything the site
dereferences should live on our disk.
