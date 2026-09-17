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

- **Rename**: trivial, no dependencies — a name field + `UPDATE`.
- **Edit start/end**: reuse the exact creation-flow slider UI, pre-populated
  from `source_activity_id`'s track (Phase A.1) and the segment's current
  geometry. Editing the geometry invalidates existing `segment_efforts` (they
  were computed against the old line) — the update path should delete them
  and re-run `match_segment_against_activities`, same as a fresh creation.
  Worth a confirmation step ("this will recompute effort history") since it's
  a real, visible change to PR history.
- ✅ **Done (2026-09-18).** "…" action menu on segment detail —
  `<details class="action-menu">`/`<summary>` dropdown holding Rescan and
  Delete (Edit will join once it exists). Close-on-outside-click is the one
  bit of real JS needed (native `<details>` doesn't do this itself) — added
  to `base.html` alongside the existing theme-toggle script rather than
  duplicated per-template, since this is a reusable component other pages
  will adopt the same way, not page-specific state. No new dependency.
- **Delete**: ✅ done as part of Phase A.4 above.

## Phase C — richer segment detail view

Once Phase A.1–A.3 land:

- Stat cards above the map (matching the existing `.stat-grid`/`.stat-tile`
  pattern already used on activity detail): distance, elevation gain, average
  grade, min/max elevation.
  - **Average grade** definition to settle: net `(end_elevation -
    start_elevation) / distance` (simple, matches most "climb segment" use
    cases) vs. accumulated-gain-based (sum of positive deltas / distance,
    more meaningful for undulating segments). Recommend net grade as the
    primary stat, since it's what "is this segment a climb or a descent"
    actually means — could add accumulated gain as a secondary stat if
    undulating segments turn out to need it.
- Segment's own elevation profile on its detail page (currently only exists
  on the *creation* page) — same Chart.js pattern, now with real data derived
  on the fly (Phase A.2).
- "My efforts" scatter — already built; no changes needed here, just noting
  it satisfies this ask already.
- **Compare-to-self over time windows.** The effort table currently only
  highlights one global all-time PR. Add windowed bests alongside it — e.g.
  "Best this year" / "Best all-time" as separate stat cards or table
  callouts, computed the same way as the existing `RANK() OVER (...)` query
  but with an added `achieved_at >= :window_start` filter for the windowed
  version. This is the wilkr-shaped equivalent of Strava's "compare to
  yourself" views — no multi-user leaderboard needed for it, just a second
  ranking scoped by date instead of scoped by nothing.

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
