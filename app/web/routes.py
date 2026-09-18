from __future__ import annotations

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
    GarminSyncState,
    Segment,
    Sport,
    Stream,
    Track,
    User,
)
from app.segments.geometry import compute_segment_geometry, find_nearest_track_index
from app.segments.matching import find_similar_segments, match_segment_against_activities
from app.web.formatting import (
    decimate,
    format_date,
    format_date_short,
    format_distance,
    format_duration,
    format_elevation,
    format_rank,
    format_speed,
    format_time_ago,
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

    stream_rows = list(
        (
            await db.execute(select(Stream).where(Stream.activity_id == activity_id))
        )
        .scalars()
        .all()
    )
    streams = {s.type: decimate(s.data) for s in stream_rows}

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

    return templates.TemplateResponse(
        request,
        "activities/detail.html",
        {
            "activity": activity,
            "geojson": geojson,
            "streams": streams,
            "laps": laps,
            "segment_efforts": segment_efforts,
        },
    )


# --------------------------------------------------------------------------
# segments
# --------------------------------------------------------------------------

async def _get_the_user_id(db: AsyncSession) -> int | None:
    return await db.scalar(select(User.id).limit(1))


async def _fetch_segments_page(
    db: AsyncSession, offset: int
) -> tuple[list[Segment], bool]:
    result = await db.execute(
        select(Segment)
        .options(joinedload(Segment.sport))
        .order_by(Segment.created_at.desc())
        .offset(offset)
        .limit(PAGE_SIZE + 1)
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_more


@router.get("/segments")
async def segment_list(request: Request, db: AsyncSession = Depends(get_db)):
    segments, has_more = await _fetch_segments_page(db, 0)
    return templates.TemplateResponse(
        request,
        "segments/list.html",
        {"segments": segments, "has_more": has_more, "next_offset": PAGE_SIZE},
    )


@router.get("/segments/rows")
async def segment_rows(request: Request, offset: int, db: AsyncSession = Depends(get_db)):
    segments, has_more = await _fetch_segments_page(db, offset)
    return templates.TemplateResponse(
        request,
        "segments/_rows_partial.html",
        {"segments": segments, "has_more": has_more, "next_offset": offset + PAGE_SIZE},
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

    return templates.TemplateResponse(
        request,
        "segments/detail.html",
        {"segment": segment, "geojson": geojson, "efforts": efforts},
    )
