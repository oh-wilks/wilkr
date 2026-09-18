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

# Nearest track vertex to a point, by real (geography) distance — not by
# fraction. ST_LineLocatePoint operates on the geometry's native degree
# coordinates, not meters; at this app's latitudes a degree of longitude is
# meaningfully shorter than a degree of latitude, so a fraction computed
# that way and then multiplied by the client's real-meter cumulative
# distance (used to build the edit-flow slider) lands at a different
# physical point — a few hundred meters off on a real track, confirmed by
# hand against activity 826. Finding the nearest vertex directly sidesteps
# the mismatch instead of trying to reconcile the two domains.
_NEAREST_INDEX = text(
    """
    WITH track_points AS (
        SELECT (dp.path)[1] - 1 AS idx, dp.geom AS pt
        FROM tracks t, LATERAL ST_DumpPoints(ST_Force2D(t.geom)) AS dp
        WHERE t.activity_id = :activity_id
    )
    SELECT idx
    FROM track_points
    ORDER BY pt::geography <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
    LIMIT 1
    """
)


async def find_nearest_track_index(
    db: AsyncSession, activity_id: int, lat: float, lon: float
) -> int | None:
    """0-based index into the track's vertex array (same order the client's
    `latlngs` array uses) nearest to (lat, lon) by real distance."""
    return await db.scalar(_NEAREST_INDEX, {"activity_id": activity_id, "lat": lat, "lon": lon})


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
