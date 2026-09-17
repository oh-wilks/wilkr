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


def _fill_missing_elevations(points: list[TrackPoint]) -> list[float]:
    """A genuinely missing elevation reading defaulting to 0 reads as "sea
    level" in the geometry, which shows up as a fake plunge in any
    elevation-derived stat (gain, grade, min/max — see
    app/segments/stats.py). Linearly interpolate across gaps between the
    nearest real readings instead; points before the first (or after the
    last) real reading hold that nearest value rather than extrapolating.
    """
    elevations: list[float | None] = [p.ele for p in points]
    n = len(elevations)
    known = [i for i, e in enumerate(elevations) if e is not None]
    if not known:
        return [0.0] * n

    result = list(elevations)
    for i in range(known[0]):
        result[i] = elevations[known[0]]
    for i in range(known[-1] + 1, n):
        result[i] = elevations[known[-1]]
    for a, b in zip(known, known[1:]):
        if b - a <= 1:
            continue
        start_e, end_e = elevations[a], elevations[b]
        for i in range(a + 1, b):
            result[i] = start_e + (end_e - start_e) * (i - a) / (b - a)
    return result  # type: ignore[return-value]


def points_to_linestring_z(points: list[TrackPoint]) -> WKTElement:
    elevations = _fill_missing_elevations(points)
    coords = ", ".join(f"{p.lon} {p.lat} {e}" for p, e in zip(points, elevations))
    return WKTElement(f"LINESTRING Z({coords})", srid=4326)


def write_track_streams_laps(
    session: AsyncSession, activity_id: int, parsed: ParsedActivity
) -> None:
    gps_points = [p for p in parsed.points if p.lat is not None and p.lon is not None]
    if len(gps_points) >= 2:
        # times must line up 1:1 with geom's vertices for segment matching
        # to map a located position back to a real timestamp — if even one
        # point lacks a time (the untimed-GPX case db_writer's caller
        # already has to handle elsewhere), leave the whole array NULL
        # rather than build a partially-aligned one; a track with no times
        # simply can't participate in matching, same honest degradation as
        # skipping the Track row entirely when there's no GPS at all.
        times = (
            [to_naive_utc(p.time) for p in gps_points]
            if all(p.time is not None for p in gps_points)
            else None
        )
        session.add(
            Track(
                activity_id=activity_id,
                geom=points_to_linestring_z(gps_points),
                times=times,
            )
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
