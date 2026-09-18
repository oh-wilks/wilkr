# GUI roadmap: Strava-inspired features, solo-athlete-filtered

Source: three Strava screenshots (dashboard/activity feed, My Gear, My
Segments), reviewed to find UI patterns worth borrowing. This is a working
plan, not a spec — same convention as `segments_roadmap.md`: phases ordered
loosely by dependency/effort, update as items ship.

## The filter: does the value come from other people, or from your own data?

wilkr is single-user and self-hosted by design (per the project charter) —
there's no one else's activity to compare against, no feed to be seen on, no
audience. Every Strava feature below gets asked one question: **is this
feature's value created by *other people* (being seen, compared, followed,
competed against, discovered by), or by *your own data accumulating over
time*?** The former gets dropped outright — it has no wilkr-shaped
equivalent, it's just absent. The latter gets adopted, sometimes reframed
around "you vs. your own history" instead of "you vs. everyone" — the same
move `segments_roadmap.md`'s Phase C already made for leaderboards (drop
KOM/QOM, keep "compare to yourself, this year / all-time").

Worked examples from the three screenshots:

| Strava feature | Value source | Verdict |
|---|---|---|
| Kudos, comments | other people's reactions | drop |
| Following / followers / clubs / suggested friends | social graph | drop |
| "Explore Segments" (public discovery) | other users' segments | drop — no other users exist |
| Men's/Women's crown (KOM/QOM) columns | global leaderboard | drop (already decided in `segments_roadmap.md`) |
| Give a Gift / Start Trial / membership status | monetization | drop |
| Starred segments | *your own* quick-access shortlist | keep |
| "My PR" / "My Goal" columns | your own history + your own target | keep |
| Relative Effort / training-load insight | derived from your own HR history | keep |
| Streak calendar | your own consistency, no audience | keep |
| Gear (bike/shoe) mileage | your own equipment wear | keep |

If a future idea doesn't obviously sort into this table, that's the
question to ask about it before scoping it: who is it for, you or an
audience you don't have?

## Screenshot 1 — Dashboard / activity feed

**Gap worth naming up front**: wilkr has no dashboard at all right now —
`/` (`app/web/routes.py:72`) redirects straight to `/activities`. The
activity list *is* the homepage. Everything below is genuinely new surface,
not a rework of something existing.

Elements seen, sorted by the filter above:

- **Keep, adapt**: a real home page combining —
  - Latest-activity highlight (already have the data — most recent
    `Activity` row).
  - Weekly streak strip (M–S, which days had an activity) — pure derived
    query over `Activity.started_at`, no new model.
  - Activity feed as cards (reuse the existing `.activity-row` pattern from
    `/activities`; a map-thumbnail version is a nice-to-have, not required
    — a static tile image per activity is extra work — decimated polyline
    traced onto a small canvas, or a tiny Leaflet instance per card — for a
    feed that's otherwise just rows).
  - Relative-Effort-style training-load insight ("Recovery week" /
    "Overreaching" style text + a small sparkline). This is the one item
    here that's a real sub-feature, not just a new template: needs a
    per-activity effort score (Strava's is HR-based, e.g. TRIMP-style —
    time-in-HR-zone weighted sum) before there's anything to plot or
    summarize. Worth its own scoping pass when picked up, not folded into
    a "just build the dashboard" task.
- **Drop**: following/followers counts, clubs, suggested friends,
  challenges, kudos/comments. "Achievements" (trophy count on each
  activity) is Strava's mix of personal PRs and segment-effort placements —
  wilkr already has the personal-PR half via segment effort ranks
  (`format_rank`); no separate achievements system needed, just surface
  what already exists.

## Screenshot 2 — My Gear

**Smallest lift of the three** — the backend already exists and is unused:
`app/models/equipment.py` (`Equipment`: name/type/brand/model/purchased_at/
retired_at, `type` constrained to shoe/bike/ski/other) plus
`equipment_sports` (default-sport mapping, e.g. "Ride" → a specific bike)
plus `Activity.equipment_id` (already joinloaded in `activity_detail` and
displayed — read-only — next to the activity meta). There is currently no
way to actually *create* equipment or *assign* it to an activity through the
UI at all; someone would have to write directly to the database.

- **Keep, adapt**:
  - A `/gear` page: My Bikes / My Shoes (grouped by `type`, matching the
    screenshot's grouping), cumulative distance per item (`SUM(Activity.
    distance_m) WHERE equipment_id = ...` — real query, not stored/denormalized,
    same "compute on the fly" call as segment elevation stats), retire/
    reactivate (`retired_at` already modeled for this), add/edit.
  - Default-sport mapping UI (`equipment_sports`) so a new activity's sport
    can pre-suggest the right gear, same convenience as Strava's table
    layout (Default Sport → Bike).
  - Making the existing read-only `activity.equipment` display on activity
    detail into an actual editable field — right now the column is wired up
    but there's no path to ever set it.
- **Drop**: Social Connections (Garmin Connect / MyFitnessPal linking —
  wilkr's Garmin sync is a backend importer, not a user-facing "connect an
  account" flow), membership status / trial upsell.

## Screenshot 3 — My Segments

- Strava's four tabs (Starred / Created / Hidden / Explore) mostly collapse
  for a single-user app: "Created Segments" is just `/segments` — every
  segment already belongs to you, there's no separate "created by others"
  set to distinguish it from. "Explore Segments" (browse the public segment
  database) drops — there is no public segment database, only yours.
  "Hidden" doesn't have an obvious use case yet either — the existing
  search/filter/sort on `/segments` (Phase E) already handles decluttering
  a long list; revisit only if that stops being enough.
- **Keep, promote from "Optional" to scoped**: starred/favorite segments.
  `segments_roadmap.md` already flagged this as optional and
  lower-priority than search/filter — this screenshot is a concrete
  reference for the UI (a star toggle + a filtered view), worth pulling
  off the optional pile now that there's a real shape to build against.
  Needs: a `starred` boolean or join table on `Segment` (per-user, though
  wilkr's segments are already implicitly per-user), a star toggle on
  segment list rows and detail, a "starred only" filter option alongside
  the existing sport/search/sort filters.
  Explicitly *not* a signal to anyone else — pure personal shortlist, same
  as a bookmarks list.
- **Keep, new idea**: "My Goal" — a per-segment target time, shown next to
  the existing PR/rank stat tiles on segment detail (`compare-to-self`
  already computes "best this year" / "all-time PR"; a goal is just a
  user-set number compared against those, e.g. "PR 5:16 · Goal 5:00 · 16s
  to go"). Small addition: one nullable column, one small form, one
  comparison line — no new page.
- **Drop**: Men's/Women's crown columns (global KOM/QOM — already decided
  against in `segments_roadmap.md`'s "explicitly out of scope").

## Staged plan

Ordered by effort and dependency, same convention as `segments_roadmap.md`
(each phase mostly unblocks or at least doesn't block the next; not a strict
priority ranking). Nothing here is started yet.

### Phase A — Gear ✅ Done (2026-09-17)

Backend already existed (`app/models/equipment.py`, `Activity.equipment_id`)
and was unused — this phase was UI + queries only, no new modeling decisions.

1. ✅ `GET /gear` — list page, grouped by `type` (bikes / shoes / other,
   matching the screenshot's grouping), retired items collapsed under a
   "+N retired" disclosure the way segments handle overflow lists. Per-item
   cumulative distance via `SUM(Activity.distance_m) WHERE equipment_id =
   :id` — computed on the fly, same call as segment elevation stats, not
   stored.
2. ✅ `POST /gear` / `POST /gear/{id}/update` — add/edit form (name, type,
   brand, model, purchased_at). Reuse the existing stat-tile form styling
   from `segments/new.html`.
   **Real bug hit and fixed**: `purchased_at` was passed to
   `CAST(:purchased_at AS date)` as a plain form string — asyncpg resolves
   the parameter's target type from the CAST, then binary-encodes the
   Python argument directly as that type; it does not parse ISO date
   strings itself. Failed with `'str' object has no attribute 'toordinal'`
   the first time a real date was set (not on NULL, so easy to miss in
   testing). Fixed by parsing to a real `datetime.date` in Python before
   binding (`_parse_form_date`), same shape as the existing
   `int(sport_id) if sport_id else None` fix for the segments filter bar —
   a CAST only resolves Postgres's parameter-type ambiguity, the Python
   value must already be the real target type.
3. ✅ `POST /gear/{id}/retire` — single toggle endpoint (flips `retired_at`
   between `NULL` and `CURRENT_DATE`) rather than separate retire/reactivate
   routes.
4. ✅ Default-sport mapping UI over `equipment_sports` — checkboxes per
   sport on the add/edit form (`gear/_form.html`), replace-all on save
   (`_set_equipment_sports`: delete then reinsert, same pattern as segment
   effort recompute on edit).
5. ✅ Turned `activity.equipment`'s read-only display into an editable
   inline `<select>` (`POST /activities/{id}/equipment`) — this was the one
   item that closed a real gap: nothing in the UI could previously set
   `equipment_id` at all. Deliberately *not* filtered to active gear only:
   a past activity may have genuinely used since-retired gear, so the
   dropdown lists everything and labels retired options rather than hiding
   them (hiding would make an already-correct historical assignment look
   unset the moment the gear was retired).

**Follow-up fix, found via step 4**: the default-sport checkboxes exposed a
pre-existing limitation — every cycling sub-type (road/MTB/gravel/indoor/
virtual/etc.) collapsed into one generic `"cycling"` `Sport` row, so there
was no way to tag a bike as an MTB-specific default. Root cause:
`sport_mapping.py` (Strava's bulk CSV importer) deliberately collapses
cycling because the CSV export has no sub-type field to disambiguate —
that's a real, permanent data limitation for historical Strava-imported
activities. `garmin_sport_mapping.py` (the Garmin-sync importer) doesn't
have that limitation — Garmin's API sends real, distinct typeKeys
(`road_biking`, `mountain_biking`, `gravel_cycling`, ...) — but was
collapsing them to match Strava's bucket anyway, purely for continuity.
Split going forward: Garmin-synced activities now get their real
sub-type as its own `Sport` row; historical Strava-imported cycling stays
under the generic `"cycling"` row permanently (can't be retroactively
split, the source data doesn't have it). Also fixed `Sport.name`'s stale
model comment (mentioned `road_cycling`/`mtb`, which nothing ever actually
produced) via migration `0007`.
**Real consequence, not just cosmetic**: segment matching filters on exact
`sport_id` (`app/segments/matching.py`), so this also splits segment
matching per cycling sub-type going forward — a segment from a future MTB
ride won't match a future gravel ride on the same physical trail, nor any
historical Strava-imported "cycling" activity. Flagged and confirmed
before implementing, not a silent side effect.

**Second refinement pass, after using the built feature**:
- The add-gear form moved out of `/gear` (it was sitting inline at the
  bottom of the list, permanently taking up space) into its own page,
  `GET /gear/new` — reuses the same `gear/_form.html` partial edit already
  used, just a third route (`new`/`create`/`edit`/`update`) pointing at it
  instead of a second copy.
- New `GET /gear/{id}` detail page. The per-row "…" action menu on the list
  is gone — clicking a gear item's *name* (not the whole row) now
  navigates there, with a row-level hover background as the "this is
  clickable" cue (`table.laps tbody tr[data-equipment-id]:hover` in
  `app.css` — background only, no `cursor: pointer`, since unlike the
  segment-effort rows the whole row isn't actually clickable, only the
  name link is). Edit/Retire moved onto the detail page's own action menu,
  same `<details class="action-menu">` pattern as segment detail.
  `gear_create`/`gear_update`/`gear_retire` now redirect to the detail
  page instead of the list, matching how segment create/rename/rescan
  already redirect to segment detail rather than back to `/segments`.
- **Explicitly declined, staying out of scope**: a components table
  (chain/tires wear on bikes, bindings/boots on skis, etc.) — considered
  and turned down, since it isn't a Strava feature that's actually been
  used. Not building it; noted here so it isn't proposed again without new
  information changing that.

### Phase B — Segment starring + "My Goal" ✅ Done (2026-09-18)

Both are small, additive changes to the already-built segments feature —
no new page, extends `/segments` and segment detail.

1. ✅ Migration `0008`: added `Segment.starred: bool` (default `false`)
   and `Segment.goal_time_s: int | None` in the same migration — both
   simple nullable/defaulted columns on the same table.
2. ✅ Star toggle — a shared Jinja **macro** (`segments/_star_button.html`,
   `star_button(segment_id, starred)`), not a plain include, specifically
   because the toggle needs to render identically from three different
   context shapes: a raw-SQL row in `/segments`' list, a `Segment` ORM
   object on detail, and just a bare `(id, starred)` pair in the
   `POST /segments/{id}/star` toggle response itself — a macro takes
   explicit params so all three call sites stay consistent without
   relying on a same-named `segment` variable happening to be in scope.
   Single toggle endpoint (flip, not separate star/unstar), same pattern
   as gear's retire toggle. Wired via `hx-post`/`hx-target="this"`/
   `hx-swap="outerHTML"` rather than a form-post-and-redirect — toggling a
   star from the list shouldn't navigate away or lose scroll position/
   active filters, and toggling from detail shouldn't force a full reload
   either.
3. ✅ Extended the existing filter bar with a "Starred only" checkbox,
   same `hx-trigger`-driven pattern as Phase E's other filters. An
   unchecked checkbox is omitted from form submission entirely (not sent
   as `"false"`), so this needed the same `str | None` + truthiness
   handling as `sport_id` already uses, not a plain `bool` param.
4. ✅ Goal-setting form on segment detail — one text input (`mm:ss` or
   `h:mm:ss`, parsed by a new `_parse_goal_time`), `POST
   /segments/{id}/goal`. Unparsable input clears the goal rather than
   erroring — there's nothing meaningfully sharper to validate against at
   this scale, and the field round-trips through the same format
   `format_duration` already displays elsewhere.
5. ✅ Comparison line next to the goal input: "🎯 goal reached" once
   PR ≤ goal, otherwise "`{duration}` to go" — computed from `efforts`
   (already loaded for the PR/best-this-year tiles), no new query.

### Phase C — Dashboard / home page

The only phase that's a genuinely new page rather than additions to an
existing one — sequence it so something ships before the open-ended part
(training-load insight) is fully scoped.

1. Decide the route: repoint `/` (`app/web/routes.py:72`) to render the
   dashboard directly instead of redirecting to `/activities`, and add
   "Dashboard" as the first link in `base.html`'s nav (`/activities`
   remains the full list/history view, same relationship Strava's own
   dashboard-vs-full-feed has).
2. Latest-activity highlight — most recent `Activity` row, already a
   one-query fetch (`_fetch_page`'s query, `LIMIT 1`).
3. Weekly streak strip (M–S, which days had ≥1 activity) — a derived
   `GROUP BY date(started_at)` query over the current week, no new model.
4. Activity feed — reuse `.activity-row` as-is for v1 (sport tag, name,
   date, key stat, same as `/activities`); a map-thumbnail card upgrade is
   a real separate cost (either a decimated-polyline canvas sketch or a
   tiny Leaflet instance per card) and shouldn't block shipping the feed
   itself.
5. **Sub-phase, scope separately**: Relative-Effort-style training-load
   insight. Needs an actual effort-scoring approach before there's
   anything to plot (Strava's is HR-based, TRIMP-style: a time-in-HR-zone
   weighted sum per activity) — this is a small algorithm-design task in
   its own right, not a template addition, so it shouldn't be estimated as
   part of "build the dashboard." Do steps 1–4 first, come back to this
   once there's a concrete scoring formula to implement.

### Phase D — Settings: in-app Garmin credential management

Not from a screenshot — a gap noticed while using the app. Today,
`GARMIN_EMAIL`/`GARMIN_PASSWORD` are plaintext env vars in
`docker-compose.yml`/`.env`, read by `_build_client()` in
`app/importers/garmin.py`. Rotating a password or re-authenticating means
editing that file and redeploying the whole stack — real friction for
something that should just be a settings-page edit, and it means the
credentials sit in `docker inspect`/`docker compose config` output
indefinitely, not just for the moment of use.

Checked before scoping this (not assuming it was buildable): the installed
`garminconnect` library (`.venv/lib/.../garminconnect/__init__.py`)
already supports a clean two-step, non-blocking MFA flow —
`Garmin(email, password, return_on_mfa=True).login(tokenstore)` returns
`(mfa_status, client_state)` immediately instead of blocking on `input()`
the way today's `prompt_mfa` callback does, and a separate
`resume_login(client_state, mfa_code)` call finishes authentication once
the code is known. That's exactly the shape a web form needs (submit
email/password → maybe show an MFA field → submit code), so this isn't
blocked on the library — it's UI + storage + rewiring `_build_client()`.

One real architectural point: `garmin-sync` runs as its own container
(`command: ["python", "-m", "app.importers.garmin", "--loop"]`), separate
from `api` (the web app) — but both already share the same Postgres via
`DATABASE_URL`. Storing credentials in Postgres rather than per-container
env vars is what actually lets a form in `api` reach the process that
needs them, with no new plumbing between containers.

1. Storage: a single-row table for credentials (same "single-user app,
   one row" convention as `GarminSyncState`) — kept separate from
   `GarminSyncState` itself rather than added to it, since auth config and
   sync-run health are different lifecycles (one changes when a password
   changes, the other on every run).
   **Open question, not decided here**: store the password as plaintext in
   Postgres, or symmetric-encrypt it with a key that itself lives in an
   env var? Worth being honest about the trade-off rather than assuming
   encryption is free: it doesn't remove the "a secret lives in an env
   var" problem, it shrinks it from two credentials to one key, and moves
   the plaintext password out of `docker inspect`/compose-file visibility
   into "queryable by anything with DB access" instead — a real but
   different exposure, consistent with wilkr's existing trust boundary
   (single user, Tailscale-only, root-on-the-box is already trusted).
2. `/settings` (or `/settings/garmin`) page: email/password form, saves to
   the new table. Add "Settings" to `base.html`'s nav.
3. Rewire `_build_client()` (`app/importers/garmin.py:124`) to read
   credentials from the DB instead of `os.environ.get(...)`. Also update
   `_scrub_secrets()` (same file, line 74) — its whole job is keeping
   credentials out of error messages that get stored in `import_events`
   and rendered on the web viewer, so it needs to scrub whatever the
   *current* credential source is, not stay hardcoded to the old env vars.
4. Browser-driven login/re-auth: a "Connect Garmin" action on the settings
   page that attempts login synchronously in the request — on
   `mfa_status`, persist `client_state` against a short-lived pending-login
   record and show an MFA-code field; submitting it calls `resume_login`.
   **Scope this action deliberately narrow**: it's for the interactive
   case (initial setup, or re-auth after a token goes stale) — it does
   *not* try to inject a web form into the unattended `--loop` background
   job. When the scheduled sync hits MFA mid-loop with no request in
   flight to answer it, today's `_fail_fast_mfa()` behavior (fail loudly,
   point at the manual re-auth path) stays correct — it just points at the
   new "Connect Garmin" button instead of `--login-only` once this ships.
5. `--login-only` (the current interactive CLI flow) can stay as a
   fallback escape hatch for SSH-only access — no need to remove it, just
   stop treating it as the primary path once step 4 exists.
