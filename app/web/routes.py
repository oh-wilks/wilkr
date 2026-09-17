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
from app.segments.matching import match_segment_against_activities
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
    db: AsyncSession = Depends(get_db),
):
    fractions = (
        await db.execute(
            text(
                """
                SELECT
                    ST_LineLocatePoint(ST_Force2D(t.geom), ST_SetSRID(ST_MakePoint(:start_lon, :start_lat), 4326)) AS f1,
                    ST_LineLocatePoint(ST_Force2D(t.geom), ST_SetSRID(ST_MakePoint(:end_lon, :end_lat), 4326)) AS f2
                FROM tracks t
                WHERE t.activity_id = :activity_id
                """
            ),
            {
                "activity_id": activity_id,
                "start_lat": start_lat,
                "start_lon": start_lon,
                "end_lat": end_lat,
                "end_lon": end_lon,
            },
        )
    ).first()

    if fractions is None or abs(fractions.f1 - fractions.f2) < 1e-6:
        activity = await db.scalar(
            select(Activity)
            .options(joinedload(Activity.sport))
            .where(Activity.id == activity_id)
        )
        geojson = await db.scalar(
            select(func.ST_AsGeoJSON(Track.geom)).where(Track.activity_id == activity_id)
        )
        sports = list((await db.execute(select(Sport).order_by(Sport.name))).scalars().all())
        return templates.TemplateResponse(
            request,
            "segments/new.html",
            {
                "activity": activity,
                "geojson": geojson,
                "sports": sports,
                "error": "Those two points are too close together — pick two points further apart along the track.",
            },
        )

    # Not wrapped in `async with db.begin()` — the fractions SELECT above
    # already auto-began a transaction on this session (SQLAlchemy 2.0
    # autobegin), so an explicit .begin() here would raise "a transaction
    # is already begun". Reuse the auto-begun transaction and commit it
    # explicitly instead — get_db()'s session is closed (not committed) on
    # request end, so this commit is what actually makes the write durable.
    new_id = (
        await db.execute(
            text(
                """
                INSERT INTO segments (name, sport_id, geom)
                SELECT :name, :sport_id,
                    ST_LineSubstring(
                        ST_Force2D(t.geom),
                        LEAST(CAST(:f1 AS double precision), CAST(:f2 AS double precision)),
                        GREATEST(CAST(:f1 AS double precision), CAST(:f2 AS double precision))
                    )
                FROM tracks t
                WHERE t.activity_id = :activity_id
                RETURNING id
                """
            ),
            {
                "name": name,
                "sport_id": sport_id,
                "activity_id": activity_id,
                "f1": fractions.f1,
                "f2": fractions.f2,
            },
        )
    ).scalar_one()
    await match_segment_against_activities(db, new_id)
    await db.commit()

    return RedirectResponse(url=f"/segments/{new_id}", status_code=303)


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
