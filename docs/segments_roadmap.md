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

## Phase D — effort-level analysis

The "do we need an effort table" question — answered above: no new table,
derive from existing `streams` sliced to `[achieved_at, achieved_at +
elapsed_time_s]` on the parent activity.

- Effort detail view (`/segments/{id}/efforts/{effort_id}` or similar):
  speed/pace and HR mini-charts for just that effort's time window, derived
  from the parent activity's existing `heart_rate`/`speed` streams — no
  schema change needed.
- Needs graceful degradation: Strava GPX-only imports may lack speed/HR/power
  streams depending on the source file: show whatever streams exist, same
  pattern activity detail already uses (`{% if streams.get(type) %}`).
- Natural follow-on once this exists: compare two efforts side by side
  (overlay two speed/HR profiles for the same segment) — not now, just
  flagging it as the obvious next step once single-effort detail works.

## Phase E — discovery, as segment count grows

- Search/filter/sort on `/segments` (by sport, by distance, most recent
  effort) — the list is a flat unsorted table today; fine at a handful of
  segments, won't stay fine.
- Optional: a map-based segment explorer (all segments as an overlay,
  independent of any one activity) — a real Strava feature, meaningfully
  more work (needs its own map + viewport-based segment loading), lower
  priority than the above.
- Optional: starred/favorite segments — less critical than on Strava, since
  wilkr is single-user and everything already belongs to you; mainly useful
  once the segment count is large enough that surfacing a "favorites" subset
  on a dashboard actually helps.

## Explicitly out of scope, with reasoning

- **Multi-user leaderboards (KOM/QOM)**: wilkr is single-user and
  self-hosted by design (per the project charter) — there's no "everyone
  who's ridden this segment" to rank against. Comparing against *yourself*
  over time (Phase C) is the wilkr-shaped equivalent, and is in scope — it's
  specifically the multi-user social layer that isn't.
- **Social features** (kudos, comments, following): not part of wilkr's
  charter; skip.
