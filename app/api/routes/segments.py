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
