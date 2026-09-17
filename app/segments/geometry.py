"""Shared geometry construction for segment create/edit.

Both POST /segments (create) and POST /segments/{id}/update (edit) need to
turn two clicked/dragged lat/lon points on a specific activity's track into
a segment's LineString geometry — same computation either way, just a
different destination (INSERT vs UPDATE) for the result.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_FRACTIONS = text(
    """
    SELECT
        ST_LineLocatePoint(ST_Force2D(t.geom), ST_SetSRID(ST_MakePoint(:start_lon, :start_lat), 4326)) AS f1,
        ST_LineLocatePoint(ST_Force2D(t.geom), ST_SetSRID(ST_MakePoint(:end_lon, :end_lat), 4326)) AS f2
    FROM tracks t
    WHERE t.activity_id = :activity_id
    """
)

_SUBSTRING_WKT = text(
    """
    SELECT ST_AsText(ST_LineSubstring(
        ST_Force2D(t.geom),
        LEAST(CAST(:f1 AS double precision), CAST(:f2 AS double precision)),
        GREATEST(CAST(:f1 AS double precision), CAST(:f2 AS double precision))
    ))
    FROM tracks t
    WHERE t.activity_id = :activity_id
    """
)


async def compute_segment_geometry(
    db: AsyncSession,
    activity_id: int,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
) -> tuple[float, float, str] | None:
    """Returns (start_fraction, end_fraction, new_geom_wkt), or None if the
    two points are too close together to form a meaningful segment (or the
    activity has no track)."""
    fractions = (
        await db.execute(
            _FRACTIONS,
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
        return None

    geom_wkt = await db.scalar(
        _SUBSTRING_WKT,
        {"activity_id": activity_id, "f1": fractions.f1, "f2": fractions.f2},
    )
    return fractions.f1, fractions.f2, geom_wkt
