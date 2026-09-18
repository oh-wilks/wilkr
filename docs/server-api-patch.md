# Server-side JSON API patch (apply on the Linux host)

Status as of 2026-09-18: `wilkr_server` currently mounts only `health_router`
and `web_router` in `app/main.py`. There is no `/api/v1/*` JSON API — the
Segments feature (Phase 2 of the charter) was built entirely as a
server-rendered Jinja2 web app (`app/web/routes.py` + templates). A stale
compiled `activities.cpython-311.pyc` with no matching `.py` source suggests
a JSON `/api/v1/activities` route existed at some point and was removed in
favor of the web app.

This patch restores/adds the JSON routes the iOS app needs, by reusing the
exact same DB queries the web app already runs — same data, second output
format (JSON instead of HTML), new URL prefix (`/api/v1/...`) alongside the
existing web pages. Nothing about the existing web app changes.

**Do not apply this from the Mac.** `wilkr_ios/docs` on the Mac used to be a
symlink into `wilkr_server/docs`, which is a Mutagen mirror synced
one-way, Linux → Mac only. Edits to `wilkr_server` made on the Mac never
reach Linux and may get overwritten on the next sync. Copy the snippets
below onto the Linux host directly and restart the server process there.

---

## 1. New file: `app/schemas/segment.py`

```python
from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.activity import SportOut


class SegmentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sport: SportOut
    created_at: datetime.datetime


class SegmentListResponse(BaseModel):
    segments: list[SegmentSummary]
    page: int
    limit: int
    has_more: bool


class SegmentEffortOut(BaseModel):
    id: int
    activity_id: int
    activity_name: str
    elapsed_time_s: int
    achieved_at: datetime.datetime
    rank: int


class SegmentDetail(SegmentSummary):
    geojson: dict | None
    efforts: list[SegmentEffortOut]
```

## 2. Replace: `app/schemas/__init__.py`

```python
from app.schemas.activity import (
    ActivityDetail,
    ActivityListResponse,
    ActivitySummary,
    EquipmentOut,
    LapOut,
    SportOut,
)
from app.schemas.segment import (
    SegmentDetail,
    SegmentEffortOut,
    SegmentListResponse,
    SegmentSummary,
)

__all__ = [
    "ActivityDetail",
    "ActivityListResponse",
    "ActivitySummary",
    "EquipmentOut",
    "LapOut",
    "SegmentDetail",
    "SegmentEffortOut",
    "SegmentListResponse",
    "SegmentSummary",
    "SportOut",
]
```

## 3. New file: `app/api/routes/activities.py`

```python
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
```

## 4. New file: `app/api/routes/segments.py`

```python
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.session import get_db
from app.models import Segment, User
from app.schemas.segment import (
    SegmentDetail,
    SegmentEffortOut,
    SegmentListResponse,
    SegmentSummary,
)

router = APIRouter(prefix="/api/v1", tags=["segments"])


async def _get_the_user_id(db: AsyncSession) -> int | None:
    return await db.scalar(select(User.id).limit(1))


@router.get("/segments", response_model=SegmentListResponse)
async def list_segments(
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=100),
    sport_id: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> SegmentListResponse:
    offset = (page - 1) * limit
    query = (
        select(Segment)
        .options(joinedload(Segment.sport))
        .order_by(Segment.created_at.desc())
    )
    if sport_id is not None:
        query = query.where(Segment.sport_id == sport_id)

    result = await db.execute(query.offset(offset).limit(limit + 1))
    rows = list(result.scalars().all())
    has_more = len(rows) > limit

    return SegmentListResponse(
        segments=[SegmentSummary.model_validate(s) for s in rows[:limit]],
        page=page,
        limit=limit,
        has_more=has_more,
    )


@router.get("/segments/{segment_id}", response_model=SegmentDetail)
async def get_segment(
    segment_id: int, db: AsyncSession = Depends(get_db)
) -> SegmentDetail:
    segment = await db.scalar(
        select(Segment).options(joinedload(Segment.sport)).where(Segment.id == segment_id)
    )
    if segment is None:
        raise HTTPException(status_code=404, detail="Segment not found")

    geojson_raw = await db.scalar(
        select(func.ST_AsGeoJSON(Segment.geom)).where(Segment.id == segment_id)
    )
    geojson = json.loads(geojson_raw) if geojson_raw else None

    user_id = await _get_the_user_id(db)
    rows = (
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

    base = SegmentSummary.model_validate(segment).model_dump()
    return SegmentDetail(
        **base,
        geojson=geojson,
        efforts=[
            SegmentEffortOut(
                id=r.id,
                activity_id=r.activity_id,
                activity_name=r.activity_name,
                elapsed_time_s=r.elapsed_time_s,
                achieved_at=r.achieved_at,
                rank=r.rank,
            )
            for r in rows
        ],
    )
```

## 5. Replace: `app/main.py`

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes.activities import router as activities_router
from app.api.routes.health import router as health_router
from app.api.routes.segments import router as segments_router
from app.web.routes import router as web_router

app = FastAPI(title="wilkr")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(health_router)
app.include_router(activities_router)
app.include_router(segments_router)
app.include_router(web_router)
```

---

## After pasting these in on Linux

Restart whatever process runs the server (systemd service, tmux/uvicorn
session, docker container — whichever is in use) so it picks up the new
code. Then these should respond with JSON:

- `GET /api/v1/activities`
- `GET /api/v1/activities/{id}`
- `GET /api/v1/segments`
- `GET /api/v1/segments/{id}`

`WilkrAPIClient.swift` already calls the first two, so activities should
start working against the real server immediately. Segments need the
iOS-side work in `ios-segments-roadmap.md` before the app can consume them.
