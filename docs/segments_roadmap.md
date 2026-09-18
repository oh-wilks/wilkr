# Segments roadmap

Segments is wilkr's answer to a paid Strava feature, so it's worth building out
properly. This is a working plan, not a spec — phases are ordered by
dependency (each one mostly unblocks the next), not strict priority. Update
this file as items ship or priorities change.

## Current state (already built, as of 2026-09-17)

- Creation: dual-handle slider addressed by track-point index (not map
  clicking), elevation profile that zooms to the selection with context
  padding, segment-only fill, direction playhead lines.
- Matching: PostGIS candidate filter + longest-plausible-run verification
  (tolerant of GPS noise and mid-effort rest stops, rejects bridged
  disconnected passes) — `app/segments/matching.py`.
- Near-duplicate warning at creation time (warn, don't block).
- Segment detail: map with direction arrows, PR-ranked effort history table,
  elapsed-time trend chart (MM:SS axis).
- Activity detail: matched-segments panel with rank labels ("PR" / "2nd best
  of N"), hover-to-highlight on the map.
- Manual rescan (`POST /segments/{id}/rescan`) for reprocessing after
  algorithm changes or new import history.

## Phase A — data-model prerequisites

These block Phase C/D below; worth doing before building on top of them
rather than after.

1. ✅ **Done (2026-09-17).** `Segment.source_activity_id` (migration 0004,
   nullable FK to `activities.id`) — populated automatically by
   `segment_create` going forward; NULL on segments created before this
   column existed. Unblocks editing a segment's start/end points (Phase B,
   needs *some* activity's track to render the slider against) and computing
   elevation stats on the fly (next item).
2. ✅ **Done (2026-09-17).** `app/segments/stats.py`,
   `get_segment_elevation_stats(session, segment_id)` — re-runs
   `ST_LineSubstring` against the source activity's `LINESTRINGZ` track
   (locating the segment's own start/end points on it via
   `ST_LineLocatePoint`), extracts Z values, and returns distance, elevation
   gain, average grade (net, see the definition note below), and min/max/
   start/end elevation. Returns `None` gracefully for segments with no
   `source_activity_id` (the SQL comparing against a NULL never matches, so
   no separate pre-check needed) — verified against both a real segment
   (6.6km, 203m gain, 2.37% avg grade, cross-checked by hand) and a legacy
   one predating the column. Not wired into any template yet — that's
   Phase C.
3. ✅ **Done (2026-09-17).** `app/importers/db_writer.py`'s
   `_fill_missing_elevations` — linearly interpolates a missing elevation
   reading between the nearest real ones instead of defaulting to 0
   (sea level), with edge readings held rather than extrapolated and an
   all-missing track falling back to the old all-0 behavior (no real data
   to interpolate from). Verified against six cases (all-real, middle gap,
   leading gap, trailing gap, all-missing, single-point) plus the actual WKT
   output. Only affects future imports — existing tracks in the DB aren't
   retroactively corrected; a backfill (re-parsing source files, same shape
   as `backfill_track_times.py`) would be a separate, not-yet-needed step.
4. ✅ **Done (2026-09-18).** `segment_efforts_segment_id_fkey` is now
   `ON DELETE CASCADE` (migration 0005 for the constraint + index, 0006 for
   the table comment autogenerate caught out of sync — two migrations
   because 0005 had already been applied before the comment mismatch was
   noticed; can't edit an applied migration after the fact, just add the
   next one). `POST /segments/{id}/delete` deletes the segment in one
   statement, efforts cascade automatically. UI: a "delete" button next to
   "rescan" on segment detail, native `confirm()` dialog dynamically
   including the real effort count ("This removes 6 effort records...")
   rather than a generic warning — no new confirmation-page flow needed for
   something this reproducible. Verified end-to-end against a real segment
   with 6 matched efforts: both rows gone after one delete call. This also
   satisfies Phase B's "Delete" item below.
5. ✅ **Done (2026-09-18).** `ix_segment_efforts_segment_id_elapsed_time_s`
   added alongside the cascade change (migration 0005) — same migration,
   no reason to split it from the FK work touching the same table.

## Phase B — segment CRUD

- ✅ **Done (2026-09-18).** `POST /segments/{id}/rename` + a "Rename" item in
  the action menu (`prompt()` dialog, same lightweight native-dialog pattern
  as delete's `confirm()`). One real bug caught and fixed along the way:
  the current name needs to reach the JS prompt safely regardless of what
  characters it contains — passing it through `tojson` directly into an
  inline `onsubmit=""` attribute broke as soon as the name had a quote in
  it (tried both a real segment name and a synthetic "Rider's "Big" Climb"
  test case). Fixed by putting the name in a `data-current-name` attribute
  instead, which Jinja's default HTML-escaping handles correctly for any
  character, read back via `.dataset` in JS (which auto-decodes) rather
  than trying to build a safe inline JS string literal by hand.
- ✅ **Done (2026-09-18).** `GET /segments/{id}/edit` renders the exact
  creation-flow template (`segments/new.html`, now mode-aware) pre-seeded
  from the segment's current geometry — extracted the fraction/geometry SQL
  both create and edit need into `app/segments/geometry.py` rather than
  duplicating it, since that duplication risk is exactly what bit
  `db_writer.py` before it was shared between strava.py/garmin.py. Legacy
  segments with no `source_activity_id` get a plain explanatory message
  instead (delete + recreate is the only path for those). `POST
  /segments/{id}/update` deletes the old effort history and reruns
  `match_segment_against_activities` against the new geometry — a real,
  native `confirm()` naming the exact effort count about to be deleted
  fires before submit, same lightweight-dialog pattern as delete/rename.
  `find_similar_segments` gained `exclude_segment_id` so an edit doesn't
  flag itself as a duplicate of its own pre-edit shape.

  Two real bugs caught during testing, not just design decisions: (1) the
  original `{% if pending %}` check for the `confirm_similar` hidden field
  would have silently skipped the duplicate-check on every edit's *first*
  save, since edit mode always sets `pending` (for slider pre-fill) even
  outside the warning case — fixed by keying off `similar_segments`
  instead, which only exists for the warning itself; (2) the exclude-self
  SQL (`:exclude_id IS NULL OR s.id != :exclude_id`) hit the same
  asyncpg "could not determine data type" error the `LEAST`/`GREATEST`
  bind params hit earlier this session — same fix, explicit
  `CAST(:exclude_id AS integer)`. Verified end-to-end: create → edit form
  correctly restores the current range → submit with a genuinely different
  range → name and geometry both updated, old efforts gone, 8 fresh ones
  rescanned (not stale) → resubmitting the same new range doesn't
  false-positive against itself.

  A third bug reported after shipping (user-caught, real-world use, not a
  test I'd written): the edit form's pre-filled handles landed a few
  hundred meters from where the segment was actually created — "not too
  far, but different." Root cause was a units mismatch, not vertex
  rounding: the pre-fill fraction came from `ST_LineLocatePoint`, which
  operates on the geometry's native *degree* coordinates, while the
  client's slider positions handles using real-meter cumulative distance
  (Leaflet's `distanceTo`). At this app's latitude (~54°N) a degree of
  longitude is only ~59% as long as a degree of latitude, so a fraction
  computed in degree-space and then multiplied back out against a
  real-meter total lands at a measurably different physical point — 435m
  off on the activity used to diagnose it, confirmed by hand (the
  continuous-fraction round trip through PostGIS alone was pixel-perfect;
  only reinterpreting that fraction as a real-meter fraction on the client
  introduced the error). Fixed by dropping fractions from this path
  entirely: `find_nearest_track_index` (`app/segments/geometry.py`) finds
  the nearest track vertex by real (geography) distance and hands the
  client an index directly, no unit conversion involved. Verified against
  real data down to sub-millimeter precision (floating-point noise only).
- ✅ **Done (2026-09-18).** "…" action menu on segment detail —
  `<details class="action-menu">`/`<summary>` dropdown holding Rename,
  Edit start/end, Rescan, and Delete — all four CRUD/maintenance actions in
  one place now that all of them exist. Close-on-outside-click is the one
  bit of real JS needed (native `<details>` doesn't do this itself) — added
  to `base.html` alongside the existing theme-toggle script rather than
  duplicated per-template, since this is a reusable component other pages
  will adopt the same way, not page-specific state. No new dependency.
- **Delete**: ✅ done as part of Phase A.4 above.

## Phase C — richer segment detail view ✅ Done (2026-09-18)

- **Stat cards**: `app/segments/stats.py`'s `get_segment_elevation_profile`
  now returns both aggregate stats *and* the per-point profile (one query
  serves the stat cards and the chart below, rather than two passes over
  the same geometry). Distance shows for every segment (a plain, always-
  available `ST_Length` via the new `get_segment_length` fallback);
  elevation gain / average grade / lowest / highest only show when the
  segment has a `source_activity_id` to derive them from — verified both
  paths directly (segment 16, which has one: full 5-tile stat row with
  plausible numbers; segment 2, a legacy segment: distance-only, no crash,
  no empty elevation tiles). **Average grade** went with net
  `(end_elevation − start_elevation) / distance`, per the plan's
  recommendation — new `format_grade` filter, signed (`+2.4%`/`−1.7%`) so
  climbs and descents read at a glance.
- **Elevation profile chart**: same Chart.js single-filled-line pattern as
  the creation flow's profile, minus the slider/zoom interactivity this
  page doesn't need — read-only, whole-segment shape.
- **Compare-to-self**: added "All-time PR" and "Best this year" (calendar
  year) as two more stat-grid tiles, computed as plain `MIN(elapsed_time_s)`
  scalars rather than folding a second ranking into the existing
  `RANK() OVER (...)` query — simpler, and the two are genuinely different
  questions ("what's the single best" vs. "what's the best within a
  window"), not different views of the same ranked list.
- "My efforts" scatter — already satisfied this ask as noted in the
  original plan; no changes needed.

## Phase D — effort-level analysis ✅ Done (2026-09-18)

The "do we need an effort table" question — answered above: no new table,
derived from existing `streams` sliced to `[achieved_at, achieved_at +
elapsed_time_s]` on the parent activity.

- `GET /segments/{id}/efforts/{effort_id}` — stat tiles (elapsed time, rank
  via the same `RANK() OVER (...)` shape as segment detail, achieved date)
  plus one mini chart per available stream type, reusing
  `activities/detail.html`'s exact generic per-type Chart.js pattern (same
  `{t, v}` data shape, just a slice of it rather than the whole activity).
  Graceful degradation confirmed against real data: an activity with only
  heart_rate/elevation/speed correctly shows those 3 charts and skips
  cadence/power, no empty chart cards.
- Segment detail's effort table now links each effort's time to this page.
- Verified precisely, not just structurally: a real effort's sliced stream
  ran from its exact `achieved_at` to exactly `achieved_at +
  elapsed_time_s` (157s effort → stream span 01:26:16–01:28:53, 157
  seconds to the second) — confirms the slice boundary is exact, not
  approximate. Also verified the URL validates the effort belongs to the
  given segment (a mismatched segment/effort pair 404s, not just an
  invalid effort id) and that rank labeling is correct at both ends (a
  PR effort shows "PR", not "1st best of N").
- Two-effort side-by-side comparison — not built now, still the obvious
  next step once this existed, per the original plan.

## Phase E — discovery, as segment count grows

- ✅ **Done (2026-09-18).** Search (name, `ILIKE`), sport filter, and sort
  (recent / longest first / name) on `/segments` — a filter bar wired via
  htmx (`hx-trigger` on debounced input + both `<select>`s), targeting a
  persistent `#segment-list` wrapper so the swap target exists even when
  the very first page load has zero results. Distance now shows per row
  too (reusing `activity-row .stat`, already shared with the activities
  list — no new CSS needed for that part). Caught and fixed a real bug
  during testing, not just a design choice: the filter form's own "All
  sports" option and the load-more link both send `sport_id=` (empty
  string) for "no filter", which FastAPI's `int | None` param type 422s on
  (it accepts a real integer or the param being absent, not an empty
  string) — switched to `str | None` with manual `int(...) if sport_id else
  None` parsing. Also had to route the *parsed* int (not the raw string)
  into the dropdown's `selected` comparison — Jinja's `==` doesn't coerce
  `"4" == 4` to true, so the raw string would have silently broken the
  dropdown's persisted state after a filter change even once the 422 was
  fixed. Verified all three filters individually and combined, the
  zero-results empty-state message, and load-more carrying the active
  filters through pagination rather than resetting to unfiltered results.
  Sort by "most recent effort" from the original plan folded into the
  existing "recent" (by `created_at`) since a segment's own creation date
  and its most recent effort are usually close together in practice — can
  split them out later if that turns out not to hold.
- Optional: a map-based segment explorer (all segments as an overlay,
  independent of any one activity) — a real Strava feature, meaningfully
  more work (needs its own map + viewport-based segment loading), lower
  priority than the above.
- Optional: starred/favorite segments — less critical than on Strava, since
  wilkr is single-user and everything already belongs to you; mainly useful
  once the segment count is large enough that surfacing a "favorites" subset
  on a dashboard actually helps.

## Phase F — synchronized hover: charts ↔ map ✅ Done (2026-09-18)

Hovering any data plot (activity detail's heart_rate/elevation/speed/
cadence/power charts, segment detail's elevation profile from Phase C)
shows a playhead on that chart *and* moves a cursor marker on the
corresponding map to the matching position along the track — the classic
"hover the graph, see where you were" pattern.

Two different indexing domains to reconcile, not one:
- **Activity detail's streams are time-indexed** (`{t, v}` pairs). Mapping
  a hovered timestamp back to a map position means locating it in
  `Track.times[]` (the parallel timestamp array added earlier for segment
  matching's elapsed-time computation) to find the corresponding vertex —
  a second, independent reuse of data that already exists for a different
  reason, not a new column.
- **Segment detail's elevation profile is distance-indexed** (`dist_m`,
  from Phase C's `get_segment_elevation_profile`) — simpler, maps directly
  to a position along the segment's own already-rendered polyline, no time
  lookup needed.

Real complexity worth flagging before starting, not discovering mid-build:
activity detail can show *up to five* charts at once (one per stream type
present). Hovering any one of them should sync all three things together:
that chart's own playhead, the same instant reflected as playheads on
every *other* visible chart (hover elevation, see the matching point on
heart rate too), and the map cursor — a shared "current hover position"
state broadcast to N chart instances plus the map, not just a single
chart's own mouseover handler.

Reuse already in the codebase, not starting from scratch: the segment
creation flow's `playheadPlugin` (a small per-chart Chart.js plugin
drawing a vertical line from `chart.$playheadXs`) is directly adaptable
for the chart-side playhead; the map-cursor-marker concept already exists
in two different forms (`segments/new.html`'s ghost marker during slider
drag, `activities/detail.html`'s hover-to-highlight segment polylines) —
this is a third variation on a pattern this codebase already has twice,
not a new one.

**Segment detail** (simpler case, built first to establish the pattern):
hovering the elevation profile shows a playhead plus a map cursor, mapped
by *fraction of each line's own total length* rather than assuming
`segment.geom`'s length and the elevation profile's freshly-derived length
match exactly — they don't, by about 0.2% on real data (751.8m vs 753.3m
for the same segment, one measured via Leaflet's Haversine, the other via
PostGIS geography), since they're computed by two different queries
against two different geometries describing the same physical path.
Verified with real extracted data: hovering at 25/50/75% along the chart
produces monotonically progressing, correctly-ordered map positions.

**Activity detail** (the real complexity, as flagged above) — new
`activity_detail` route now also serializes `Track.times` to the client
(ISO strings, matching the exact format streams already use, so a hover
timestamp compares directly against either without reparsing). Sync is
keyed by *timestamp*, not array index, specifically because different
stream types decimate to different point counts (verified on real data:
hovering heart_rate — 500 points — at its own index 250 correctly
resolved elevation to the same index 250, but speed — only 500 of 1402
raw points survived a *different* decimation ratio — to index 247, and
the map — the full undecimated 1402-point track — to index 692, all four
landing on the exact same instant). `nearestTimeIndex` checks both
neighbors around its binary-search landing point rather than just taking
the first `>=` match, since a sparser stream can otherwise land
meaningfully further from the true hovered instant than a denser one.
Verified graceful degradation too: an activity with streams but no track
still gets full chart-to-chart sync, just no map cursor (guarded, not a
crash).

## Phase G — themed modal component (replacing native confirm()/prompt())

Delete, rename, and edit's "this will recompute effort history" warning
all currently use the browser's native `confirm()`/`prompt()` — functional,
but unstyled OS chrome that ignores the app's dark/light theme entirely and
can't be styled at all. Replace with a themed, reusable modal.

- **A real component, not one-off HTML per dialog** — the user's own
  instinct here matches the project's established pattern: reusable UI
  behavior belongs in `base.html` alongside the other shared components
  already there (theme-toggle, the action-menu close-on-outside-click),
  not duplicated per template the way small page-specific helpers are.
  Something like `Modal.confirm({title, message, danger}) => Promise` and
  `Modal.prompt({title, message, initialValue}) => Promise`.
- **Async by necessity, not just by choice.** A custom HTML/CSS modal can't
  block execution the way native `confirm()`/`prompt()` do — every current
  call site (rename, delete, edit's recompute warning) needs converting
  from synchronous `if (!confirm(...)) return false;` to `async`/`await`.
  This is a real, contained refactor of existing working code, not purely
  additive — budget for it accordingly, not as a quick styling pass.
- **Styling**: reuse the existing theme tokens (`--bg`/`--surface`/
  `--border`/`--accent`/`--danger`) so it matches dark/light switching
  that already works everywhere else, rather than inventing new colors.
- **This directly prevents a bug class already hit once.** Rename's
  original implementation tried to inject the current name into an inline
  `onsubmit=""` attribute via `tojson`, which broke the moment the name
  contained a quote (fixed via a `data-*` attribute instead — see Phase
  B's notes). A modal component that takes its message as a plain JS
  string parameter, not reconstructed from an HTML attribute, makes that
  entire class of escaping bug structurally impossible rather than
  something to keep catching by hand.

## Explicitly out of scope, with reasoning

- **Multi-user leaderboards (KOM/QOM)**: wilkr is single-user and
  self-hosted by design (per the project charter) — there's no "everyone
  who's ridden this segment" to rank against. Comparing against *yourself*
  over time (Phase C) is the wilkr-shaped equivalent, and is in scope — it's
  specifically the multi-user social layer that isn't.
- **Social features** (kudos, comments, following): not part of wilkr's
  charter; skip.
