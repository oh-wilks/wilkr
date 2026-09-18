from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.session import get_db
from app.models import Activity, ActivityLap, Stream, Track
from app.schemas import (
    ActivityDetail,
    ActivityListResponse,
    ActivitySummary,
    EquipmentOut,
    LapOut,
)
from app.web.formatting import decimate

router = APIRouter(prefix="/api/v1", tags=["activities"])


@router.get("/activities", response_model=ActivityListResponse)
async def list_activities(
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=100),
    sport_id: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> ActivityListResponse:
    offset = (page - 1) * limit
    query = (
        select(Activity)
        .options(joinedload(Activity.sport))
        .order_by(Activity.started_at.desc())
    )
    if sport_id is not None:
        query = query.where(Activity.sport_id == sport_id)

    result = await db.execute(query.offset(offset).limit(limit + 1))
    rows = list(result.scalars().all())
    has_more = len(rows) > limit

    return ActivityListResponse(
        activities=[ActivitySummary.model_validate(a) for a in rows[:limit]],
        page=page,
        limit=limit,
        has_more=has_more,
    )


@router.get("/activities/{activity_id}", response_model=ActivityDetail)
async def get_activity(
    activity_id: int, db: AsyncSession = Depends(get_db)
) -> ActivityDetail:
    activity = await db.scalar(
        select(Activity)
        .options(joinedload(Activity.sport), joinedload(Activity.equipment))
        .where(Activity.id == activity_id)
    )
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    geojson_raw = await db.scalar(
        select(func.ST_AsGeoJSON(Track.geom)).where(Track.activity_id == activity_id)
    )
    track_geojson = json.loads(geojson_raw) if geojson_raw else None

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

    base = ActivitySummary.model_validate(activity).model_dump()
    return ActivityDetail(
        **base,
        description=activity.description,
        equipment=EquipmentOut.model_validate(activity.equipment)
        if activity.equipment
        else None,
        laps=[LapOut.model_validate(lap) for lap in laps],
        streams=streams,
        track_geojson=track_geojson,
    )
