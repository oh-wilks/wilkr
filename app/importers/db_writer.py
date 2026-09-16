"""Shared DB-writing logic for import pipelines.

Extracted from app/importers/strava.py once app/importers/garmin.py needed
the identical tracks/streams/activity_laps writing code — both pipelines
produce the same ParsedActivity shape (see parsers/common.py) regardless
of source, so this is the one place that turns that shape into rows.
"""

from __future__ import annotations

import datetime

from geoalchemy2.elements import WKTElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.importers.parsers.common import ParsedActivity, TrackPoint
from app.models import ActivityLap, Stream, Track

STREAM_EXTRACTORS = {
    "heart_rate": lambda p: p.hr,
    "elevation": lambda p: p.ele,
    "speed": lambda p: p.speed,
    "cadence": lambda p: p.cadence,
    "power": lambda p: p.power,
}


def to_naive_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


def points_to_linestring_z(points: list[TrackPoint]) -> WKTElement:
    coords = ", ".join(f"{p.lon} {p.lat} {p.ele or 0}" for p in points)
    return WKTElement(f"LINESTRING Z({coords})", srid=4326)


def write_track_streams_laps(
    session: AsyncSession, activity_id: int, parsed: ParsedActivity
) -> None:
    gps_points = [p for p in parsed.points if p.lat is not None and p.lon is not None]
    if len(gps_points) >= 2:
        session.add(
            Track(activity_id=activity_id, geom=points_to_linestring_z(gps_points))
        )

    for stream_type, extractor in STREAM_EXTRACTORS.items():
        series = [
            {"t": to_naive_utc(p.time).isoformat(), "v": extractor(p)}
            for p in parsed.points
            if p.time is not None and extractor(p) is not None
        ]
        if series:
            session.add(Stream(activity_id=activity_id, type=stream_type, data=series))

    for lap in parsed.laps:
        session.add(
            ActivityLap(
                activity_id=activity_id,
                lap_index=lap.lap_index,
                lap_type="active",
                started_at=to_naive_utc(lap.started_at),
                elapsed_time_s=lap.elapsed_time_s,
                distance_m=lap.distance_m,
                elevation_change_m=lap.elevation_change_m,
                avg_speed_mps=lap.avg_speed_mps,
                max_speed_mps=lap.max_speed_mps,
            )
        )


def first_timed_point(parsed: ParsedActivity) -> TrackPoint | None:
    return next((p for p in parsed.points if p.time is not None), None)
