"""Derived, read-time segment statistics.

Segment.geom is flattened to 2D at creation (see segment_create), so
elevation-based stats aren't stored on the segment itself — they're
re-derived here from the segment's source activity's own 3D track on every
call. That avoids duplicating data that already exists on the source
activity, and means these stats can never drift out of sync with it.
Cheap enough at this app's scale to recompute per page view rather than
cache.

Distinct from app/segments/matching.py, which is about finding which
activities achieved a segment, not describing the segment's own shape.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ELEVATION_POINTS = text(
    """
    WITH seg AS (
        SELECT geom, source_activity_id FROM segments WHERE id = :segment_id
    ),
    sub3d AS (
        SELECT ST_LineSubstring(t.geom, LEAST(fracs.f1, fracs.f2), GREATEST(fracs.f1, fracs.f2)) AS geom
        FROM tracks t, seg,
        LATERAL (
            SELECT
                ST_LineLocatePoint(ST_Force2D(t.geom), ST_StartPoint(seg.geom)) AS f1,
                ST_LineLocatePoint(ST_Force2D(t.geom), ST_EndPoint(seg.geom)) AS f2
        ) fracs
        WHERE t.activity_id = seg.source_activity_id
    )
    SELECT ST_Z(dp.geom) AS elevation
    FROM sub3d, LATERAL ST_DumpPoints(sub3d.geom) AS dp
    ORDER BY (dp.path)[1]
    """
)

_SEGMENT_LENGTH = text("SELECT ST_Length(geom::geography) FROM segments WHERE id = :segment_id")


async def get_segment_elevation_stats(session: AsyncSession, segment_id: int) -> dict | None:
    """Returns None if the segment has no recorded source activity
    (created before source_activity_id existed) or that activity's track
    is unavailable — the SQL naturally returns zero rows in either case
    (comparing against a NULL source_activity_id never matches), no
    explicit pre-check needed.
    """
    rows = (await session.execute(_ELEVATION_POINTS, {"segment_id": segment_id})).scalars().all()
    elevations = [e for e in rows if e is not None]
    if len(elevations) < 2:
        return None

    gain_m = sum(max(0.0, b - a) for a, b in zip(elevations, elevations[1:]))
    distance_m = await session.scalar(_SEGMENT_LENGTH, {"segment_id": segment_id})
    avg_grade_pct = ((elevations[-1] - elevations[0]) / distance_m * 100) if distance_m else None

    return {
        "distance_m": distance_m,
        "elevation_gain_m": gain_m,
        "avg_grade_pct": avg_grade_pct,
        "min_elevation_m": min(elevations),
        "max_elevation_m": max(elevations),
        "start_elevation_m": elevations[0],
        "end_elevation_m": elevations[-1],
    }
