from __future__ import annotations

import datetime
import pathlib

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.session import get_db
from app.models import (
    Activity,
    ActivityLap,
    Equipment,
    GarminSyncState,
    Segment,
    Sport,
    Stream,
    Track,
    User,
)
from app.segments.geometry import compute_segment_geometry, find_nearest_track_index
from app.segments.matching import find_similar_segments, match_segment_against_activities
from app.segments.stats import get_segment_elevation_profile, get_segment_length
from app.web.formatting import (
    decimate,
    format_date,
    format_date_short,
    format_distance,
    format_duration,
    format_elevation,
    format_grade,
    format_rank,
    format_speed,
    format_time_ago,
    speed_stream_for_display,
)

router = APIRouter()

TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["duration"] = format_duration
templates.env.filters["distance"] = format_distance
templates.env.filters["elevation"] = format_elevation
templates.env.filters["date"] = format_date
templates.env.filters["date_short"] = format_date_short
templates.env.filters["speed"] = format_speed
templates.env.filters["time_ago"] = format_time_ago
templates.env.filters["rank_label"] = format_rank
templates.env.filters["grade"] = format_grade


def static_url(path: str) -> str:
    # Cache-bust on the file's own mtime — browsers cache /static/* with no
    # Cache-Control header, so an unchanged URL after a CSS/JS edit can
    # silently keep serving the stale copy until a hard refresh.
    file_path = pathlib.Path("app/static") / path
    try:
        version = int(file_path.stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{path}?v={version}"


templates.env.globals["static_url"] = static_url

PAGE_SIZE = 30


@router.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse(url="/activities")


async def _fetch_page(db: AsyncSession, offset: int) -> tuple[list[Activity], bool]:
    result = await db.execute(
        select(Activity)
        .options(joinedload(Activity.sport))
        .order_by(Activity.started_at.desc())
        .offset(offset)
        .limit(PAGE_SIZE + 1)
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_more


@router.get("/activities")
async def activity_list(request: Request, db: AsyncSession = Depends(get_db)):
    activities, has_more = await _fetch_page(db, 0)
    garmin_sync = await db.scalar(select(GarminSyncState))
    return templates.TemplateResponse(
        request,
        "activities/list.html",
        {
            "activities": activities,
            "has_more": has_more,
            "next_offset": PAGE_SIZE,
            "garmin_sync": garmin_sync,
        },
    )


@router.get("/activities/rows")
async def activity_rows(
    request: Request, offset: int, db: AsyncSession = Depends(get_db)
):
    activities, has_more = await _fetch_page(db, offset)
    return templates.TemplateResponse(
        request,
        "activities/_rows_partial.html",
        {
            "activities": activities,
            "has_more": has_more,
            "next_offset": offset + PAGE_SIZE,
        },
    )


@router.get("/activities/{activity_id}")
async def activity_detail(
    request: Request, activity_id: int, db: AsyncSession = Depends(get_db)
):
    activity = await db.scalar(
        select(Activity)
        .options(joinedload(Activity.sport), joinedload(Activity.equipment))
        .where(Activity.id == activity_id)
    )
    if activity is None:
        return templates.TemplateResponse(
            request, "activities/not_found.html", {}, status_code=404
        )

    geojson = await db.scalar(
        select(func.ST_AsGeoJSON(Track.geom)).where(Track.activity_id == activity_id)
    )
    track_times = await db.scalar(
        select(Track.times).where(Track.activity_id == activity_id)
    )
    # Parallel to geojson's (undecimated) coordinates array, 1:1 — same
    # invariant segment matching already relies on. Serialized to ISO
    # strings here since tojson can't encode raw datetime objects and the
    # stream {t, v} pairs already use this exact string format (both come
    # from the same to_naive_utc(...).isoformat() convention), so a hover
    # timestamp compares directly against either without reparsing.
    times_iso = [t.isoformat() for t in track_times] if track_times else None

    stream_rows = list(
        (
            await db.execute(select(Stream).where(Stream.activity_id == activity_id))
        )
        .scalars()
        .all()
    )
    streams = {s.type: decimate(s.data) for s in stream_rows}
    speed_mode = "kmh"
    if "speed" in streams:
        streams["speed"], speed_mode = speed_stream_for_display(
            streams["speed"], activity.sport.name
        )

    laps = list(
        (
            await db.execute(
                select(ActivityLap)
                .where(ActivityLap.activity_id == activity_id)
                .order_by(ActivityLap.lap_index)
            )
        )
        .scalars()
        .all()
    )

    segment_efforts = (
        await db.execute(
            text(
                """
                WITH ranked AS (
                    SELECT se.id, se.segment_id, se.activity_id, se.elapsed_time_s, se.achieved_at,
                           RANK() OVER (PARTITION BY se.segment_id ORDER BY se.elapsed_time_s) AS rank,
                           COUNT(*) OVER (PARTITION BY se.segment_id) AS total_efforts
                    FROM segment_efforts se
                    JOIN activities a ON a.id = se.activity_id
                    WHERE a.user_id = :user_id
                )
                SELECT r.id, r.segment_id, r.elapsed_time_s, r.rank, r.total_efforts,
                       s.name AS segment_name, ST_AsGeoJSON(s.geom) AS geom_json
                FROM ranked r
                JOIN segments s ON s.id = r.segment_id
                WHERE r.activity_id = :activity_id
                ORDER BY r.id
                """
            ),
            {"user_id": activity.user_id, "activity_id": activity_id},
        )
    ).all()

    # No retired_at filter here (unlike the /gear default-sport pickers) —
    # a past activity may genuinely have used gear that's since been
    # retired, and correcting/back-filling that assignment should still be
    # possible; the template just labels retired options for clarity.
    gear_options = list(
        (
            await db.execute(
                select(Equipment)
                .where(Equipment.user_id == activity.user_id)
                .order_by(Equipment.name)
            )
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "activities/detail.html",
        {
            "activity": activity,
            "geojson": geojson,
            "times": times_iso,
            "streams": streams,
            "laps": laps,
            "segment_efforts": segment_efforts,
            "gear_options": gear_options,
            "speed_mode": speed_mode,
        },
    )


@router.post("/activities/{activity_id}/equipment")
async def activity_set_equipment(
    request: Request,
    activity_id: int,
    equipment_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text("UPDATE activities SET equipment_id = CAST(:equipment_id AS integer) WHERE id = :activity_id"),
        {"equipment_id": int(equipment_id) if equipment_id else None, "activity_id": activity_id},
    )
    await db.commit()
    return RedirectResponse(url=f"/activities/{activity_id}", status_code=303)


# --------------------------------------------------------------------------
# segments
# --------------------------------------------------------------------------

async def _get_the_user_id(db: AsyncSession) -> int | None:
    return await db.scalar(select(User.id).limit(1))


_SEGMENT_SORTS = {
    "recent": "s.created_at DESC",
    "distance": "ST_Length(s.geom::geography) DESC",
    "name": "s.name ASC",
}


async def _fetch_segments_page(
    db: AsyncSession,
    offset: int,
    q: str | None = None,
    sport_id: int | None = None,
    sort: str = "recent",
    starred_only: bool = False,
) -> tuple[list, bool]:
    order_clause = _SEGMENT_SORTS.get(sort, _SEGMENT_SORTS["recent"])
    rows = (
        await db.execute(
            text(
                f"""
                SELECT s.id, s.name, s.created_at, s.starred, sp.name AS sport_name,
                       ST_Length(s.geom::geography) AS distance_m
                FROM segments s
                JOIN sports sp ON sp.id = s.sport_id
                WHERE (CAST(:q AS text) IS NULL OR s.name ILIKE '%' || :q || '%')
                  AND (CAST(:sport_id AS integer) IS NULL OR s.sport_id = CAST(:sport_id AS integer))
                  AND (CAST(:starred_only AS boolean) = FALSE OR s.starred = TRUE)
                ORDER BY {order_clause}
                OFFSET :offset LIMIT :limit
                """
            ),
            {
                "q": q or None,
                "sport_id": sport_id,
                "starred_only": starred_only,
                "offset": offset,
                "limit": PAGE_SIZE + 1,
            },
        )
    ).all()
    has_more = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_more


def _segment_filter_context(
    q: str | None, sport_id: int | None, sort: str, starred_only: bool
) -> dict:
    return {
        "q": q or "",
        "sport_id": sport_id,
        "sort": sort if sort in _SEGMENT_SORTS else "recent",
        "starred_only": starred_only,
    }


@router.get("/segments")
async def segment_list(
    request: Request,
    q: str | None = None,
    sport_id: str | None = None,
    sort: str = "recent",
    starred: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    # sport_id arrives as a plain query string, not a typed FastAPI param —
    # both the filter form's "All sports" option and the load-more link
    # send an empty string for "no filter", which `int | None` can't parse
    # (FastAPI 422s on "" for an int param; it only accepts a real integer
    # or the param being absent entirely). Same reasoning applies to
    # `starred` — an unchecked checkbox omits the field entirely rather
    # than sending "false", so `str | None` + truthiness is what actually
    # matches how a browser submits it.
    sport_id_int = int(sport_id) if sport_id else None
    starred_only = bool(starred)
    segments, has_more = await _fetch_segments_page(db, 0, q, sport_id_int, sort, starred_only)
    sports = list((await db.execute(select(Sport).order_by(Sport.name))).scalars().all())
    return templates.TemplateResponse(
        request,
        "segments/list.html",
        {
            "segments": segments,
            "has_more": has_more,
            "next_offset": PAGE_SIZE,
            "sports": sports,
            **_segment_filter_context(q, sport_id_int, sort, starred_only),
        },
    )


@router.get("/segments/rows")
async def segment_rows(
    request: Request,
    offset: int,
    q: str | None = None,
    sport_id: str | None = None,
    sort: str = "recent",
    starred: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    sport_id_int = int(sport_id) if sport_id else None
    starred_only = bool(starred)
    segments, has_more = await _fetch_segments_page(db, offset, q, sport_id_int, sort, starred_only)
    return templates.TemplateResponse(
        request,
        "segments/_rows_partial.html",
        {
            "segments": segments,
            "has_more": has_more,
            "next_offset": offset + PAGE_SIZE,
            **_segment_filter_context(q, sport_id_int, sort, starred_only),
        },
    )


@router.get("/segments/new")
async def segment_new(
    request: Request, activity_id: int | None = None, db: AsyncSession = Depends(get_db)
):
    if activity_id is None:
        return templates.TemplateResponse(request, "segments/new.html", {"activity": None})

    activity = await db.scalar(
        select(Activity).options(joinedload(Activity.sport)).where(Activity.id == activity_id)
    )
    geojson = await db.scalar(
        select(func.ST_AsGeoJSON(Track.geom)).where(Track.activity_id == activity_id)
    )
    sports = list((await db.execute(select(Sport).order_by(Sport.name))).scalars().all())
    return templates.TemplateResponse(
        request,
        "segments/new.html",
        {"activity": activity, "geojson": geojson, "sports": sports, "error": None},
    )


@router.post("/segments")
async def segment_create(
    request: Request,
    activity_id: int = Form(...),
    name: str = Form(...),
    sport_id: int = Form(...),
    start_lat: float = Form(...),
    start_lon: float = Form(...),
    end_lat: float = Form(...),
    end_lon: float = Form(...),
    confirm_similar: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    async def _rerender(error: str | None, similar: list[dict] | None = None):
        activity = await db.scalar(
            select(Activity)
            .options(joinedload(Activity.sport))
            .where(Activity.id == activity_id)
        )
        geojson = await db.scalar(
            select(func.ST_AsGeoJSON(Track.geom)).where(Track.activity_id == activity_id)
        )
        sports = list((await db.execute(select(Sport).order_by(Sport.name))).scalars().all())
        pending = None
        if similar:
            # Carry the original slider selection through the re-render —
            # without this, the slider resets to its full-range default,
            # and clicking "Save segment" again would silently create a
            # segment from the wrong (full-track) range instead of
            # confirming what was actually picked. Looked up by nearest
            # vertex (not the fraction ST_LineLocatePoint would give) since
            # start_lat/start_lon are already the exact clicked vertex
            # coordinates the client sent — see find_nearest_track_index's
            # docstring for why fractions aren't safe to hand back to the
            # client's real-meter-distance-based slider.
            pending = {
                "name": name,
                "sport_id": sport_id,
                "start_idx": await find_nearest_track_index(db, activity_id, start_lat, start_lon),
                "end_idx": await find_nearest_track_index(db, activity_id, end_lat, end_lon),
            }
        return templates.TemplateResponse(
            request,
            "segments/new.html",
            {
                "activity": activity,
                "geojson": geojson,
                "sports": sports,
                "error": error,
                "similar_segments": similar,
                "pending": pending,
            },
        )

    result = await compute_segment_geometry(
        db, activity_id, start_lat, start_lon, end_lat, end_lon
    )
    if result is None:
        return await _rerender(
            "Those two points are too close together — pick two points further apart along the track."
        )
    _, _, new_geom_wkt = result

    if not confirm_similar:
        similar = await find_similar_segments(db, new_geom_wkt, sport_id)
        if similar:
            return await _rerender(None, similar)

    # Not wrapped in `async with db.begin()` — compute_segment_geometry's
    # SELECT above already auto-began a transaction on this session
    # (SQLAlchemy 2.0 autobegin), so an explicit .begin() here would raise
    # "a transaction is already begun". Reuse the auto-begun transaction and
    # commit it explicitly instead — get_db()'s session is closed (not
    # committed) on request end, so this commit is what actually makes the
    # write durable.
    new_id = (
        await db.execute(
            text(
                """
                INSERT INTO segments (name, sport_id, geom, source_activity_id)
                VALUES (:name, :sport_id, ST_GeomFromText(:geom_wkt, 4326), :activity_id)
                RETURNING id
                """
            ),
            {
                "name": name,
                "sport_id": sport_id,
                "geom_wkt": new_geom_wkt,
                "activity_id": activity_id,
            },
        )
    ).scalar_one()
    await match_segment_against_activities(db, new_id)
    await db.commit()

    return RedirectResponse(url=f"/segments/{new_id}", status_code=303)


@router.get("/segments/{segment_id}/edit")
async def segment_edit_form(
    request: Request, segment_id: int, db: AsyncSession = Depends(get_db)
):
    segment = await db.scalar(select(Segment).where(Segment.id == segment_id))
    if segment is None:
        return templates.TemplateResponse(
            request, "segments/not_found.html", {}, status_code=404
        )

    if segment.source_activity_id is None:
        return templates.TemplateResponse(
            request,
            "segments/new.html",
            {
                "activity": None,
                "geojson": None,
                "sports": [],
                "error": None,
                "similar_segments": None,
                "pending": None,
                "edit_unavailable": True,
            },
        )

    activity = await db.scalar(
        select(Activity)
        .options(joinedload(Activity.sport))
        .where(Activity.id == segment.source_activity_id)
    )
    geojson = await db.scalar(
        select(func.ST_AsGeoJSON(Track.geom)).where(
            Track.activity_id == segment.source_activity_id
        )
    )
    sports = list((await db.execute(select(Sport).order_by(Sport.name))).scalars().all())

    endpoints = (
        await db.execute(
            text(
                """
                SELECT
                    ST_Y(ST_StartPoint(geom)) AS start_lat, ST_X(ST_StartPoint(geom)) AS start_lon,
                    ST_Y(ST_EndPoint(geom)) AS end_lat, ST_X(ST_EndPoint(geom)) AS end_lon
                FROM segments WHERE id = :segment_id
                """
            ),
            {"segment_id": segment_id},
        )
    ).first()

    effort_count = await db.scalar(
        text("SELECT count(*) FROM segment_efforts WHERE segment_id = :segment_id"),
        {"segment_id": segment_id},
    )

    return templates.TemplateResponse(
        request,
        "segments/new.html",
        {
            "activity": activity,
            "geojson": geojson,
            "sports": sports,
            "error": None,
            "similar_segments": None,
            "pending": {
                "name": segment.name,
                "sport_id": segment.sport_id,
                "start_idx": await find_nearest_track_index(
                    db, segment.source_activity_id, endpoints.start_lat, endpoints.start_lon
                ),
                "end_idx": await find_nearest_track_index(
                    db, segment.source_activity_id, endpoints.end_lat, endpoints.end_lon
                ),
            },
            "edit_segment_id": segment_id,
            "existing_effort_count": effort_count,
        },
    )


@router.post("/segments/{segment_id}/update")
async def segment_update(
    request: Request,
    segment_id: int,
    name: str = Form(...),
    sport_id: int = Form(...),
    start_lat: float = Form(...),
    start_lon: float = Form(...),
    end_lat: float = Form(...),
    end_lon: float = Form(...),
    confirm_similar: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    segment = await db.scalar(select(Segment).where(Segment.id == segment_id))
    if segment is None or segment.source_activity_id is None:
        return templates.TemplateResponse(
            request, "segments/not_found.html", {}, status_code=404
        )
    source_activity_id = segment.source_activity_id

    async def _rerender(error: str | None, similar: list[dict] | None = None):
        activity = await db.scalar(
            select(Activity)
            .options(joinedload(Activity.sport))
            .where(Activity.id == source_activity_id)
        )
        geojson = await db.scalar(
            select(func.ST_AsGeoJSON(Track.geom)).where(Track.activity_id == source_activity_id)
        )
        sports = list((await db.execute(select(Sport).order_by(Sport.name))).scalars().all())
        effort_count = await db.scalar(
            text("SELECT count(*) FROM segment_efforts WHERE segment_id = :segment_id"),
            {"segment_id": segment_id},
        )
        pending = {"name": name, "sport_id": sport_id}
        if similar:
            pending["start_idx"] = await find_nearest_track_index(
                db, source_activity_id, start_lat, start_lon
            )
            pending["end_idx"] = await find_nearest_track_index(
                db, source_activity_id, end_lat, end_lon
            )
        return templates.TemplateResponse(
            request,
            "segments/new.html",
            {
                "activity": activity,
                "geojson": geojson,
                "sports": sports,
                "error": error,
                "similar_segments": similar,
                "pending": pending,
                "edit_segment_id": segment_id,
                "existing_effort_count": effort_count,
            },
        )

    result = await compute_segment_geometry(
        db, source_activity_id, start_lat, start_lon, end_lat, end_lon
    )
    if result is None:
        return await _rerender(
            "Those two points are too close together — pick two points further apart along the track."
        )
    _, _, new_geom_wkt = result

    if not confirm_similar:
        similar = await find_similar_segments(
            db, new_geom_wkt, sport_id, exclude_segment_id=segment_id
        )
        if similar:
            return await _rerender(None, similar)

    await db.execute(
        text(
            """
            UPDATE segments SET name = :name, sport_id = :sport_id, geom = ST_GeomFromText(:geom_wkt, 4326)
            WHERE id = :segment_id
            """
        ),
        {"name": name, "sport_id": sport_id, "geom_wkt": new_geom_wkt, "segment_id": segment_id},
    )
    # The old geometry's effort history no longer describes this segment's
    # new line — delete and let match_segment_against_activities rebuild it
    # (a fresh scan, same as segment creation, not a partial patch).
    await db.execute(
        text("DELETE FROM segment_efforts WHERE segment_id = :segment_id"),
        {"segment_id": segment_id},
    )
    await match_segment_against_activities(db, segment_id)
    await db.commit()

    return RedirectResponse(url=f"/segments/{segment_id}", status_code=303)


@router.post("/segments/{segment_id}/rescan")
async def segment_rescan(
    request: Request, segment_id: int, db: AsyncSession = Depends(get_db)
):
    segment = await db.scalar(select(Segment).where(Segment.id == segment_id))
    if segment is None:
        return templates.TemplateResponse(
            request, "segments/not_found.html", {}, status_code=404
        )
    await match_segment_against_activities(db, segment_id)
    await db.commit()
    return RedirectResponse(url=f"/segments/{segment_id}", status_code=303)


@router.post("/segments/{segment_id}/rename")
async def segment_rename(
    request: Request,
    segment_id: int,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    segment = await db.scalar(select(Segment).where(Segment.id == segment_id))
    if segment is None:
        return templates.TemplateResponse(
            request, "segments/not_found.html", {}, status_code=404
        )
    name = name.strip()
    if name:
        await db.execute(
            text("UPDATE segments SET name = :name WHERE id = :segment_id"),
            {"name": name, "segment_id": segment_id},
        )
        await db.commit()
    return RedirectResponse(url=f"/segments/{segment_id}", status_code=303)


@router.post("/segments/{segment_id}/delete")
async def segment_delete(
    request: Request, segment_id: int, db: AsyncSession = Depends(get_db)
):
    segment = await db.scalar(select(Segment).where(Segment.id == segment_id))
    if segment is None:
        return templates.TemplateResponse(
            request, "segments/not_found.html", {}, status_code=404
        )
    # segment_efforts.segment_id is ON DELETE CASCADE (migration 0005) — one
    # statement removes the segment and its effort history together.
    await db.execute(text("DELETE FROM segments WHERE id = :segment_id"), {"segment_id": segment_id})
    await db.commit()
    return RedirectResponse(url="/segments", status_code=303)


@router.post("/segments/{segment_id}/star")
async def segment_star(
    request: Request, segment_id: int, db: AsyncSession = Depends(get_db)
):
    was_starred = await db.scalar(select(Segment.starred).where(Segment.id == segment_id))
    if was_starred is None:
        return templates.TemplateResponse(
            request, "segments/not_found.html", {}, status_code=404
        )
    new_starred = not was_starred
    await db.execute(
        text("UPDATE segments SET starred = :starred WHERE id = :segment_id"),
        {"starred": new_starred, "segment_id": segment_id},
    )
    await db.commit()
    # Returns just the toggled button itself — called via hx-post/hx-swap
    # from both /segments rows and segment detail, so toggling doesn't
    # navigate away or lose list scroll position/filter state.
    return templates.TemplateResponse(
        request,
        "segments/_star_button_render.html",
        {"segment_id": segment_id, "starred": new_starred},
    )


def _parse_goal_time(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        parts = [int(p) for p in value.split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


@router.post("/segments/{segment_id}/goal")
async def segment_goal(
    request: Request,
    segment_id: int,
    goal_time: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text("UPDATE segments SET goal_time_s = CAST(:goal_time_s AS integer) WHERE id = :segment_id"),
        {"goal_time_s": _parse_goal_time(goal_time), "segment_id": segment_id},
    )
    await db.commit()
    return RedirectResponse(url=f"/segments/{segment_id}", status_code=303)


@router.get("/segments/{segment_id}")
async def segment_detail(
    request: Request, segment_id: int, db: AsyncSession = Depends(get_db)
):
    segment = await db.scalar(
        select(Segment).options(joinedload(Segment.sport)).where(Segment.id == segment_id)
    )
    if segment is None:
        return templates.TemplateResponse(
            request, "segments/not_found.html", {}, status_code=404
        )

    geojson = await db.scalar(
        select(func.ST_AsGeoJSON(Segment.geom)).where(Segment.id == segment_id)
    )

    user_id = await _get_the_user_id(db)
    efforts = (
        await db.execute(
            text(
                """
                SELECT
                    se.id, se.elapsed_time_s, se.achieved_at, se.activity_id,
                    a.name AS activity_name,
                    RANK() OVER (ORDER BY se.elapsed_time_s) AS rank
                FROM segment_efforts se
                JOIN activities a ON a.id = se.activity_id
                WHERE se.segment_id = :segment_id AND a.user_id = :user_id
                ORDER BY se.achieved_at DESC
                """
            ),
            {"segment_id": segment_id, "user_id": user_id},
        )
    ).all()

    # Calendar-year "best this year" alongside the all-time PR already in
    # `efforts` — the wilkr-shaped equivalent of Strava's compare-to-self
    # views (see docs/segments_roadmap.md's Phase C note on why a
    # multi-user leaderboard isn't the right analog here).
    year_start = datetime.datetime(datetime.datetime.now().year, 1, 1)
    best_this_year_s = await db.scalar(
        text(
            """
            SELECT MIN(se.elapsed_time_s)
            FROM segment_efforts se
            JOIN activities a ON a.id = se.activity_id
            WHERE se.segment_id = :segment_id AND a.user_id = :user_id
              AND se.achieved_at >= :year_start
            """
        ),
        {"segment_id": segment_id, "user_id": user_id, "year_start": year_start},
    )

    elevation = await get_segment_elevation_profile(db, segment_id)
    distance_m = elevation["distance_m"] if elevation else await get_segment_length(db, segment_id)

    return templates.TemplateResponse(
        request,
        "segments/detail.html",
        {
            "segment": segment,
            "geojson": geojson,
            "efforts": efforts,
            "best_this_year_s": best_this_year_s,
            "elevation": elevation,
            "distance_m": distance_m,
        },
    )


@router.get("/segments/{segment_id}/efforts/{effort_id}")
async def segment_effort_detail(
    request: Request, segment_id: int, effort_id: int, db: AsyncSession = Depends(get_db)
):
    row = (
        await db.execute(
            text(
                """
                SELECT se.id, se.segment_id, se.activity_id, se.elapsed_time_s, se.achieved_at,
                       a.name AS activity_name, s.name AS segment_name, sp.name AS sport_name
                FROM segment_efforts se
                JOIN activities a ON a.id = se.activity_id
                JOIN segments s ON s.id = se.segment_id
                JOIN sports sp ON sp.id = s.sport_id
                WHERE se.id = :effort_id AND se.segment_id = :segment_id
                """
            ),
            {"effort_id": effort_id, "segment_id": segment_id},
        )
    ).first()
    if row is None:
        return templates.TemplateResponse(
            request, "segments/not_found.html", {}, status_code=404
        )

    user_id = await _get_the_user_id(db)
    rank_row = (
        await db.execute(
            text(
                """
                WITH ranked AS (
                    SELECT se.id, RANK() OVER (ORDER BY se.elapsed_time_s) AS rank,
                           COUNT(*) OVER () AS total
                    FROM segment_efforts se
                    JOIN activities a ON a.id = se.activity_id
                    WHERE se.segment_id = :segment_id AND a.user_id = :user_id
                )
                SELECT rank, total FROM ranked WHERE id = :effort_id
                """
            ),
            {"segment_id": segment_id, "user_id": user_id, "effort_id": effort_id},
        )
    ).first()

    window_start = row.achieved_at
    window_end = row.achieved_at + datetime.timedelta(seconds=row.elapsed_time_s)

    stream_rows = list(
        (
            await db.execute(select(Stream).where(Stream.activity_id == row.activity_id))
        )
        .scalars()
        .all()
    )
    streams = {}
    for stream in stream_rows:
        sliced = [
            p
            for p in stream.data
            if window_start <= datetime.datetime.fromisoformat(p["t"]) <= window_end
        ]
        if sliced:
            streams[stream.type] = decimate(sliced)

    speed_mode = "kmh"
    if "speed" in streams:
        streams["speed"], speed_mode = speed_stream_for_display(streams["speed"], row.sport_name)

    return templates.TemplateResponse(
        request,
        "segments/effort_detail.html",
        {
            "effort": row,
            "rank": rank_row.rank if rank_row else None,
            "total_efforts": rank_row.total if rank_row else None,
            "streams": streams,
            "speed_mode": speed_mode,
        },
    )


_GEAR_TYPES = [("bike", "Bikes"), ("shoe", "Shoes"), ("ski", "Skis"), ("other", "Other")]


def _parse_form_date(value: str) -> datetime.date | None:
    # A bare CAST(:param AS date) only settles Postgres's parameter-type
    # ambiguity — asyncpg still binary-encodes the Python argument directly
    # as a date, and errors if it's handed a plain ISO string instead of a
    # real date object (it doesn't parse the string itself).
    return datetime.date.fromisoformat(value) if value else None


async def _set_equipment_sports(db: AsyncSession, equipment_id: int, sport_ids: list[int]) -> None:
    # Replace-all rather than diffing — same pattern segment editing already
    # uses for its own join-table-shaped state (segment_efforts on save):
    # simpler than computing an add/remove delta, and the set is always
    # small (a handful of sports per gear item).
    await db.execute(
        text("DELETE FROM equipment_sports WHERE equipment_id = :equipment_id"),
        {"equipment_id": equipment_id},
    )
    for sport_id in sport_ids:
        await db.execute(
            text(
                "INSERT INTO equipment_sports (equipment_id, sport_id) "
                "VALUES (:equipment_id, :sport_id)"
            ),
            {"equipment_id": equipment_id, "sport_id": sport_id},
        )


@router.get("/gear")
async def gear_list(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_the_user_id(db)
    rows = (
        await db.execute(
            text(
                """
                SELECT e.id, e.name, e.type, e.brand, e.model, e.purchased_at, e.retired_at,
                       COALESCE(SUM(a.distance_m), 0) AS distance_m
                FROM equipment e
                LEFT JOIN activities a ON a.equipment_id = e.id
                WHERE e.user_id = :user_id
                GROUP BY e.id
                ORDER BY e.name
                """
            ),
            {"user_id": user_id},
        )
    ).all()
    gear_by_type: dict[str, list] = {}
    for row in rows:
        gear_by_type.setdefault(row.type, []).append(row)
    return templates.TemplateResponse(
        request,
        "gear/list.html",
        {"gear_types": _GEAR_TYPES, "gear_by_type": gear_by_type},
    )


@router.get("/gear/new")
async def gear_new_form(request: Request, db: AsyncSession = Depends(get_db)):
    sports = list((await db.execute(select(Sport).order_by(Sport.name))).scalars().all())
    return templates.TemplateResponse(
        request,
        "gear/new.html",
        {"equipment": None, "sports": sports, "selected_sport_ids": set()},
    )


@router.post("/gear")
async def gear_create(
    request: Request,
    name: str = Form(...),
    gear_type: str = Form(...),
    brand: str = Form(""),
    model: str = Form(""),
    purchased_at: str = Form(""),
    sport_ids: list[int] = Form([]),
    db: AsyncSession = Depends(get_db),
):
    user_id = await _get_the_user_id(db)
    equipment_id = await db.scalar(
        text(
            """
            INSERT INTO equipment (user_id, name, type, brand, model, purchased_at)
            VALUES (:user_id, :name, :type, :brand, :model, CAST(:purchased_at AS date))
            RETURNING id
            """
        ),
        {
            "user_id": user_id,
            "name": name.strip(),
            "type": gear_type,
            "brand": brand.strip() or None,
            "model": model.strip() or None,
            "purchased_at": _parse_form_date(purchased_at),
        },
    )
    await _set_equipment_sports(db, equipment_id, sport_ids)
    await db.commit()
    return RedirectResponse(url=f"/gear/{equipment_id}", status_code=303)


@router.get("/gear/{equipment_id}")
async def gear_detail(request: Request, equipment_id: int, db: AsyncSession = Depends(get_db)):
    equipment = await db.scalar(select(Equipment).where(Equipment.id == equipment_id))
    if equipment is None:
        return templates.TemplateResponse(request, "gear/not_found.html", {}, status_code=404)
    distance_m = await db.scalar(
        text("SELECT COALESCE(SUM(distance_m), 0) FROM activities WHERE equipment_id = :equipment_id"),
        {"equipment_id": equipment_id},
    )
    sport_names = list(
        (
            await db.execute(
                text(
                    """
                    SELECT sp.name FROM equipment_sports es
                    JOIN sports sp ON sp.id = es.sport_id
                    WHERE es.equipment_id = :equipment_id
                    ORDER BY sp.name
                    """
                ),
                {"equipment_id": equipment_id},
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "gear/detail.html",
        {"equipment": equipment, "distance_m": distance_m, "sport_names": sport_names},
    )


@router.get("/gear/{equipment_id}/edit")
async def gear_edit_form(
    request: Request, equipment_id: int, db: AsyncSession = Depends(get_db)
):
    equipment = await db.scalar(select(Equipment).where(Equipment.id == equipment_id))
    if equipment is None:
        return templates.TemplateResponse(request, "gear/not_found.html", {}, status_code=404)
    sports = list((await db.execute(select(Sport).order_by(Sport.name))).scalars().all())
    selected_sport_ids = set(
        (
            await db.execute(
                text("SELECT sport_id FROM equipment_sports WHERE equipment_id = :equipment_id"),
                {"equipment_id": equipment_id},
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "gear/edit.html",
        {"equipment": equipment, "sports": sports, "selected_sport_ids": selected_sport_ids},
    )


@router.post("/gear/{equipment_id}/update")
async def gear_update(
    request: Request,
    equipment_id: int,
    name: str = Form(...),
    gear_type: str = Form(...),
    brand: str = Form(""),
    model: str = Form(""),
    purchased_at: str = Form(""),
    sport_ids: list[int] = Form([]),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text(
            """
            UPDATE equipment
            SET name = :name, type = :type, brand = :brand, model = :model,
                purchased_at = CAST(:purchased_at AS date)
            WHERE id = :equipment_id
            """
        ),
        {
            "name": name.strip(),
            "type": gear_type,
            "brand": brand.strip() or None,
            "model": model.strip() or None,
            "purchased_at": _parse_form_date(purchased_at),
            "equipment_id": equipment_id,
        },
    )
    await _set_equipment_sports(db, equipment_id, sport_ids)
    await db.commit()
    return RedirectResponse(url=f"/gear/{equipment_id}", status_code=303)


@router.post("/gear/{equipment_id}/retire")
async def gear_retire(
    request: Request, equipment_id: int, db: AsyncSession = Depends(get_db)
):
    # Single toggle endpoint rather than separate retire/reactivate routes —
    # retired_at is nullable-means-active (see the model's own comment), so
    # flipping between the two states is just clearing or setting one column.
    await db.execute(
        text(
            """
            UPDATE equipment
            SET retired_at = CASE WHEN retired_at IS NULL THEN CURRENT_DATE ELSE NULL END
            WHERE id = :equipment_id
            """
        ),
        {"equipment_id": equipment_id},
    )
    await db.commit()
    return RedirectResponse(url=f"/gear/{equipment_id}", status_code=303)
