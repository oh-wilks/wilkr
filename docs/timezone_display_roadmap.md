# Local timezone display

Found while verifying Phase H (`docs/segments_roadmap.md`): "una moss"'s two
real passes showed on different calendar dates (27/28 Aug) even though,
locally, they were ~38 minutes apart on the same evening. Not a Phase H bug
— a pre-existing, app-wide issue Phase H's genuinely-separate efforts just
made visible for the first time.

## Root cause

Every timestamp in wilkr is stored as naive UTC (`to_naive_utc()` in
`app/importers/db_writer.py` — correct, standard practice for storage).
Nothing converts to local time for *display*, anywhere in the app —
`format_date`/`format_date_short` (`app/web/formatting.py`) just
`.strftime()` the raw UTC value directly. A single blended effort never
crossed a visible date boundary, so this was never obviously wrong before.

Concretely: this ride is in Whistler, BC (Pacific, UTC−7 in August). Pass 1
at `23:33 UTC` → 16:33 local. Pass 2 at `00:11 UTC` (next UTC calendar day)
→ 17:11 local — still the same evening, just 38 minutes later. UTC's
midnight falls at 5pm Pacific, right in the middle of the ride, so the raw
UTC *date* flips even though the real evening didn't.

## Design: per-activity, computed at display time — not a global setting

A single configured "home timezone" would be wrong the moment an activity
happens somewhere else (travel, a race trip). The correct unit is *where
that specific activity happened* — derived from its own track's starting
GPS coordinates, computed at display time rather than stored, matching the
project's existing convention for exactly this kind of thing (segment
elevation stats, gear cumulative distance — compute on read, don't
duplicate/cache data that can drift from its source).

## No external API needed

Researched against the real package, not assumed: **`timezonefinder`**
([PyPI](https://pypi.org/project/timezonefinder/),
[GitHub](https://github.com/jannikmi/timezonefinder)) does lat/lon → IANA
timezone name (`"America/Vancouver"`) fully offline — no network call at
runtime, no API key, no rate limit. `~150KB` wheel + a separate small
`timezonefinder-data` package (fetched at **build** time via
pip/uv — same as every other Python dependency wilkr already has; zero
*runtime* network dependency, unlike the Immich photo integration
(`docs/immich_photos_roadmap.md`), which genuinely is a live external
service by design). Requires Python ≥3.11 — wilkr's Dockerfile already
targets 3.12, compatible.

Once we have the IANA name, no extra dependency is needed to actually
apply it — Python's stdlib `zoneinfo.ZoneInfo` (3.9+) localizes a naive-UTC
datetime directly: `value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))`.

## Implementation sketch

1. Add `timezonefinder` to `pyproject.toml`/`uv.lock`.
2. New helper (`app/web/formatting.py` or a new small `app/core/
   timezones.py`): given an activity's (or segment effort's parent
   activity's) starting lat/lon, `timezone_at(lng=..., lat=...)` → IANA
   name → `ZoneInfo` → localize.
3. **Fallback for activities with no GPS** (indoor trainer, treadmill —
   nothing to look up a location from): a configured `DEFAULT_TIMEZONE` env
   var as the fallback, not a hard failure.
4. **Where this actually needs wiring in** — broader than it first looks,
   not just the `format_date`/`format_date_short` filters:
   - Activity `started_at` (list + detail).
   - Segment `created_at`, segment effort `achieved_at` (segment detail's
     effort table, `effort_detail.html`).
   - Gear `purchased_at`/`retired_at` — lower priority, no GPS association
     at all (falls back to `DEFAULT_TIMEZONE` always).
   - **Chart x-axis time-of-day labels** in `activities/detail.html`, which
     currently slice the raw UTC ISO string directly
     (`(p.t || '').slice(11, 19)`) — these show UTC time-of-day today, not
     local; easy to miss since it's JS string-slicing, not a Jinja filter.
   - Segment detail's elapsed-time trend chart's per-effort date labels.
5. **Granularity decision, not resolved here**: a segment's efforts can
   come from many different activities, in principle in different
   timezones if the user ever travels. Ideally each effort's date uses
   *its own* activity's timezone, not one lookup for the whole page — but
   that means one `timezonefinder` call per distinct activity shown, not
   one per page. Given lookups are fast and local, this is likely fine,
   but worth deciding deliberately rather than defaulting to "one lookup
   per page" for simplicity and being wrong for the rare traveling-athlete
   case.

## Scope note

App-wide (activities, segments, gear), not segments-specific — a separate
concern from Phase H, even though Phase H's genuinely-correct dual-pass
efforts are what surfaced it. Not started.
