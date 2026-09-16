from __future__ import annotations

import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.session import get_db
from app.models import Activity, ActivityLap, Stream, Track
from app.web.formatting import (
    decimate,
    format_date,
    format_date_short,
    format_distance,
    format_duration,
    format_elevation,
    format_speed,
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
    return templates.TemplateResponse(
        request,
        "activities/list.html",
        {
            "activities": activities,
            "has_more": has_more,
            "next_offset": PAGE_SIZE,
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

    return templates.TemplateResponse(
        request,
        "activities/detail.html",
        {
            "activity": activity,
            "geojson": geojson,
            "streams": streams,
            "laps": laps,
        },
    )
