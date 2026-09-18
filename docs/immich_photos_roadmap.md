# Immich photo integration: feasibility + roadmap

Asked: how feasible is pulling in photos from Immich (self-hosted photo
library) and attaching them to activities and gear, adapting the schema if
it makes sense. Researched against Immich's real OpenAPI spec (server
v3.2.0, `open-api/immich-openapi-specs.json` in the `immich-app/immich`
repo) rather than assumed — the findings below are grounded in the actual
API surface, not a guess at what a photo app "probably" supports.

## Verdict: feasible, and it fits wilkr's shape well

Immich is self-hosted with a documented REST API authenticated by a
per-user API key sent as an `x-api-key` header
([docs](https://immich.app/docs/api/)) — the same "another service on your
own Tailscale network" trust model wilkr already has with Garmin. wilkr
wouldn't store any image bytes itself: Immich already hosts and serves
them, so this is a *reference*, not a duplication — the same "compute/link
on the fly, don't copy" instinct already behind segment elevation stats and
gear's cumulative-distance query.

Activities and gear turn out to need genuinely different designs, though —
not the same feature twice.

## Activities: time+location correlation, not manual picking

An activity has a time window (`started_at` → `started_at +
elapsed_time_s`) and a GPS track. The natural feature is Strava/Google
Photos-style auto-suggestion: "here are photos that were probably taken
during this ride," surfaced for the user to confirm, not blind
auto-attachment (same "suggest, don't assume" instinct as the segment
near-duplicate warning).

**The API doesn't offer this as one call — it's a two-step filter, and
that's fine.** Checked both plausible endpoints in the real spec:

- `POST /search/metadata` supports `takenAfter`/`takenBefore` (exactly what
  an activity's time window needs) but its only location filters are
  reverse-geocoded **place names** (`city`/`state`/`country`) — no raw
  lat/lon or bounding-box parameter. Too coarse: "was in Prince George" is
  useless for confirming a photo was taken *on this specific 26km ride*
  and not some other errand the same day in the same city.
- `GET /map/markers` returns every geotagged photo's `id`/`lat`/`lon`
  (plus place names), filterable by `fileCreatedAfter`/`fileCreatedBefore`
  — but again no server-side geo-bounding-box filter.

So: call `/map/markers` with the activity's time window (padded by, say,
±30min for photos taken just before/after recording started/stopped) to
get a small candidate set — a day's worth of geotagged photos, not the
whole library — then do the geo-proximity filtering **in wilkr**, against
`Track.geom`, using `ST_DWithin`/`<->` — the exact same PostGIS pattern
`app/segments/matching.py` already uses for candidate filtering. Nothing
new to learn here, just the same tool pointed at a different problem.

## Equipment: simpler, no correlation needed

A gear item doesn't have a time window or a route — there's no auto-suggest
signal to work with, and it doesn't need one. This is just "pick a photo of
your bike and attach it," a manual one-time action, most naturally via a
small search-and-pick UI backed by `/search/metadata`'s ordinary text/
filename search (no location or time logic involved at all).

## Schema

No image bytes stored — just a reference. Shape still open (not decided
here, worth a real scoping pass before building):

- **Simplest**: one small linking table,
  `photos(id, activity_id NULL, equipment_id NULL, immich_asset_id,
  taken_at, lat, lon, added_at)` with a `CHECK` that exactly one of
  `activity_id`/`equipment_id` is set — mirrors how `Activity.equipment_id`
  is already a single nullable FK rather than a separate join table.
- **Alternative**: two separate tables (`activity_photos`,
  `equipment_photos`) if the two use cases diverge enough in practice
  (e.g. gear ends up wanting exactly one "cover photo" per item, while
  activities genuinely want many) that a shared table with two purposes
  starts feeling forced.

Caching `taken_at`/`lat`/`lon` locally (not just the asset ID) avoids an
Immich round-trip just to re-render a photo strip — same reasoning as
caching `distance_m` on the gear list query, cheap and avoids a live
dependency on Immich being reachable just to load an activity page.

## Display: needs one real implementation decision, not yet resolved

Fetching thumbnail/original bytes is `GET /assets/{id}/thumbnail` /
`/assets/{id}/original`. The spec shows a `key` query parameter as an
alternate to the `x-api-key` header — if that accepts a real API key (not
just Immich's separate "shared link" tokens), `<img src="https://immich.
tailnet/api/assets/{id}/thumbnail?key=...">` could point the browser
straight at Immich. That needs verifying against a live instance before
relying on it — the spec alone doesn't say. The fallback that's guaranteed
to work regardless: proxy through wilkr's own backend (`GET
/activities/{id}/photos/{asset_id}`, wilkr attaches `x-api-key`
server-side and streams the bytes) — the same shape as how the Garmin
importer already authenticates to an external service on the user's
behalf, just synchronous/request-driven instead of a background job.

## Config

New env vars needed either way: `IMMICH_BASE_URL` + `IMMICH_API_KEY`.
Simplest to start (direct `os.environ.get(...)`, matching how
`GARMIN_EMAIL`/`GARMIN_PASSWORD` are read today, not through
`app/core/config.py`'s `Settings` — that class currently only holds
`database_url`) — worth reconsidering once Phase D (in-app Garmin
credential management, `docs/gui_roadmap.md`) exists, since at that point
there'd already be a Settings page and a established pattern for
DB-stored, UI-editable external-service credentials that Immich's API key
could reasonably follow too instead of yet another env var.

## Staged plan

1. **Schema**: pick one of the two shapes above, migration, `Photo`
   model (or `ActivityPhoto`/`EquipmentPhoto` pair).
2. **Immich client**: a small `app/integrations/immich.py` (new module,
   same shape as `app/importers/garmin.py`'s use of the `garminconnect`
   client) wrapping `httpx`/`requests` calls to `/map/markers`,
   `/search/metadata`, and the asset endpoints, with `x-api-key` auth.
3. **Equipment photos** (simpler, do first): search-and-pick UI on the gear
   edit page, save the chosen `immich_asset_id`, display via whichever
   display approach step 5 below settles on.
4. **Activity photo suggestions**: on activity detail, a "find photos from
   this ride" action calling `/map/markers` for the time window + local
   `ST_DWithin` filtering, presenting candidates to confirm/attach — not
   automatic, same "suggest, don't assume" pattern as segment near-
   duplicates.
5. **Resolve the display question** (proxy vs. direct `?key=` embed) against
   a real Immich instance before committing to one — this changes whether
   wilkr's backend is in the request path for every photo view or not,
   worth confirming rather than assuming.

Not started. No commitment yet on which schema shape or display approach —
both explicitly left open above for a real scoping pass when this gets
picked up.
