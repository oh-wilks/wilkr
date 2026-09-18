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
