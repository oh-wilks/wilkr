"""Not part of docs/server-api-patch.md as given — that doc's app/schemas
patch assumes this file already exists (it imports SportOut etc. from it)
but nothing under app/schemas/ exists in this repo or its git history.
Written to match the current ORM models field-for-field (app/models/
activity.py, activity_lap.py, equipment.py, sport.py) rather than guessed,
scoped to exactly what app/api/routes/activities.py (also from that doc)
actually constructs — no avg_hr/max_hr/calories_kcal yet since neither
route reads them; trivial to add if the iOS side needs them."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict


class SportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str


class EquipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    brand: str | None
    model: str | None


class LapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lap_index: int
    lap_type: str
    started_at: datetime.datetime
    elapsed_time_s: int
    distance_m: float | None
    elevation_change_m: float | None
    avg_speed_mps: float | None
    max_speed_mps: float | None


class ActivitySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sport: SportOut
    started_at: datetime.datetime
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    elevation_gain_m: float | None


class ActivityListResponse(BaseModel):
    activities: list[ActivitySummary]
    page: int
    limit: int
    has_more: bool


class ActivityDetail(ActivitySummary):
    description: str | None
    equipment: EquipmentOut | None
    laps: list[LapOut]
    streams: dict
    track_geojson: dict | None
