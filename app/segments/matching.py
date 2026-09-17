"""PostGIS spatial-buffer segment matching.

Two entry points, both built on the same core matcher:
- match_activity_against_segments(session, activity_id): a new activity
  just landed (Strava/Garmin import) — check it against every existing
  segment of the same sport.
- match_segment_against_activities(session, segment_id): a new segment
  was just created — check it against every existing activity of the same
  sport (the charter's "scan historical activities" requirement).

Algorithm (see the design brief in the Phase 2 plan for the full reasoning):
1. Candidate filter — ST_DWithin on ::geography casts, using the existing
   GIST indexes. Cheap, coarse, just rules out tracks nowhere near the
   segment.
2. Verification — sample points along the segment, locate each along the
   track (ST_LineLocatePoint), confirm actual proximity at that location,
   confirm the located fractions are monotonically increasing (direction-
   sensitive) across at least MIN_MATCH_RATIO of samples.
3. Elapsed time — convert the first/last passing sample's located fraction
   to the nearest tracks.times[] index and read the timestamp directly
   (vertex-level granularity, not sub-vertex interpolation).

A track with times IS NULL can't produce an effort (nothing to compute
elapsed time from) and is excluded by the candidate query itself.
"""

from __future__ import annotations

import datetime
import math

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SegmentEffort

BUFFER_M = 15
N_SAMPLES = 20
MIN_MATCH_RATIO = 0.9

_CANDIDATE_SEGMENTS_FOR_TRACK = text(
    """
    SELECT s.id
    FROM segments s
    JOIN tracks t ON t.id = :track_id
    JOIN activities a ON a.id = t.activity_id
    WHERE s.sport_id = a.sport_id
      AND ST_DWithin(s.geom::geography, t.geom::geography, :buffer_m)
    """
)

_CANDIDATE_TRACKS_FOR_SEGMENT = text(
    """
    SELECT t.id, t.activity_id
    FROM tracks t
    JOIN activities a ON a.id = t.activity_id
    JOIN segments s ON s.id = :segment_id
    WHERE a.sport_id = s.sport_id
      AND t.times IS NOT NULL
      AND ST_DWithin(s.geom::geography, t.geom::geography, :buffer_m)
    """
)

_SAMPLE_VERIFICATION = text(
    """
    WITH samples AS (
        SELECT i, ST_LineInterpolatePoint(s.geom, i::float / :n_samples) AS sample_point
        FROM segments s, generate_series(0, :n_samples) AS i
        WHERE s.id = :segment_id
    ),
    track AS (
        SELECT ST_Force2D(t.geom) AS geom2d
        FROM tracks t
        WHERE t.id = :track_id
    ),
    located AS (
        SELECT
            samples.i,
            samples.sample_point,
            ST_LineLocatePoint(track.geom2d, samples.sample_point) AS track_fraction
        FROM samples, track
    )
    SELECT
        located.i,
        located.track_fraction,
        ST_Distance(
            located.sample_point::geography,
            ST_LineInterpolatePoint(track.geom2d, located.track_fraction)::geography
        ) AS distance_m
    FROM located, track
    ORDER BY located.i
    """
)


MIN_SPEED_MPS = 0.1  # ~0.36 km/h — very lax on purpose. A real activity can
# legitimately pause for a while mid-segment (a rest break, refilling a
# bottle, waiting for a partner) without that being "two different visits".
# This floor only needs to catch the case that floor is not for: two
# genuinely disconnected passes near the same geometry, hours apart, being
# bridged into one impossible multi-hour "effort" (observed in real data at
# ~0.016 m/s — a 6x margin below this floor).


def _fraction_to_time(
    times: list[datetime.datetime], fraction: float
) -> datetime.datetime:
    idx = round(fraction * (len(times) - 1))
    idx = max(0, min(idx, len(times) - 1))
    return times[idx]


def _longest_plausible_run(
    indices: list[int],
    fractions: list[float],
    sample_times: list[datetime.datetime],
    n_samples: int,
    segment_length_m: float,
) -> list[int]:
    """Indices (into `fractions`) of the longest chain that is both
    non-decreasing in fraction and physically plausible in elapsed time.
    O(n^2) — trivial at N_SAMPLES+1 elements (~21).

    Two checks, for different failure modes:
    - fraction ordering: ST_LineLocatePoint has no concept of "which pass"
      when a sample point could plausibly correspond to more than one
      location on the track, so a real, clearly-forward traversal can still
      produce one or two isolated out-of-order fractions. Requiring the
      *whole* passing set to be strictly ordered rejects those on a single
      bad sample; this tolerates them the same way the distance check
      already tolerates a few missed samples.
    - time plausibility: fraction ordering alone isn't enough — a track that
      passes near the segment twice, hours apart (e.g. a long loop ride
      crossing a nearby road on both the outbound and return legs), can
      still look "monotonic" if both passes happen to be forward. Since
      `indices` (the samples' positions along the *segment*, by
      construction — not the located track fraction) are evenly spaced by
      `n_samples`, the real segment-distance between any two samples is
      known and fixed regardless of which candidate track is being
      checked; the elapsed time between them must be consistent with at
      least `MIN_SPEED_MPS` over that distance, or the transition is
      rejected as bridging two unrelated passes.
    """
    n = len(fractions)
    lengths = [1] * n
    prev = [-1] * n
    for j in range(n):
        for k in range(j):
            if fractions[k] > fractions[j]:
                continue
            seg_dist_m = (indices[j] - indices[k]) / n_samples * segment_length_m
            elapsed_s = (sample_times[j] - sample_times[k]).total_seconds()
            if elapsed_s > 0 and seg_dist_m / elapsed_s < MIN_SPEED_MPS:
                continue
            if lengths[k] + 1 > lengths[j]:
                lengths[j] = lengths[k] + 1
                prev[j] = k
    end = max(range(n), key=lambda idx: lengths[idx]) if n else -1
    chain = []
    while end != -1:
        chain.append(end)
        end = prev[end]
    chain.reverse()
    return chain


def _evaluate_samples(
    rows: list, times: list[datetime.datetime], segment_length_m: float
) -> tuple[float, float] | None:
    """rows: (i, track_fraction, distance_m) ordered by i. Returns
    (start_fraction, end_fraction) along the track if this is a genuine
    match, else None."""
    passing = [(i, frac) for i, frac, dist in rows if dist is not None and dist <= BUFFER_M]
    required = math.ceil(MIN_MATCH_RATIO * len(rows))
    if len(passing) < required:
        return None

    indices = [i for i, _ in passing]
    fractions = [frac for _, frac in passing]
    sample_times = [_fraction_to_time(times, frac) for frac in fractions]
    chain = _longest_plausible_run(indices, fractions, sample_times, N_SAMPLES, segment_length_m)
    if len(chain) < required:
        return None

    start_fraction = fractions[chain[0]]
    end_fraction = fractions[chain[-1]]
    if end_fraction <= start_fraction:
        return None
    return start_fraction, end_fraction


async def _effort_exists(session: AsyncSession, segment_id: int, activity_id: int) -> bool:
    existing = await session.execute(
        text(
            "SELECT 1 FROM segment_efforts WHERE segment_id = :segment_id "
            "AND activity_id = :activity_id"
        ),
        {"segment_id": segment_id, "activity_id": activity_id},
    )
    return existing.first() is not None


async def _try_match(
    session: AsyncSession, segment_id: int, track_id: int, activity_id: int
) -> SegmentEffort | None:
    if await _effort_exists(session, segment_id, activity_id):
        return None

    times = await session.scalar(
        text("SELECT times FROM tracks WHERE id = :track_id"), {"track_id": track_id}
    )
    if not times:
        return None

    segment_length_m = await session.scalar(
        text("SELECT ST_Length(geom::geography) FROM segments WHERE id = :segment_id"),
        {"segment_id": segment_id},
    )

    rows = (
        await session.execute(
            _SAMPLE_VERIFICATION,
            {"segment_id": segment_id, "track_id": track_id, "n_samples": N_SAMPLES},
        )
    ).all()
    result = _evaluate_samples(rows, times, segment_length_m)
    if result is None:
        return None
    start_fraction, end_fraction = result

    started = _fraction_to_time(times, start_fraction)
    ended = _fraction_to_time(times, end_fraction)
    elapsed_s = int((ended - started).total_seconds())
    if elapsed_s <= 0:
        return None

    effort = SegmentEffort(
        segment_id=segment_id,
        activity_id=activity_id,
        elapsed_time_s=elapsed_s,
        achieved_at=started,
    )
    session.add(effort)
    return effort


async def match_activity_against_segments(
    session: AsyncSession, activity_id: int
) -> list[SegmentEffort]:
    track_row = await session.execute(
        text("SELECT id FROM tracks WHERE activity_id = :activity_id AND times IS NOT NULL"),
        {"activity_id": activity_id},
    )
    track = track_row.first()
    if track is None:
        return []
    track_id = track[0]

    candidates = (
        await session.execute(
            _CANDIDATE_SEGMENTS_FOR_TRACK, {"track_id": track_id, "buffer_m": BUFFER_M}
        )
    ).all()

    efforts = []
    for (segment_id,) in candidates:
        effort = await _try_match(session, segment_id, track_id, activity_id)
        if effort is not None:
            efforts.append(effort)
    return efforts


async def match_segment_against_activities(
    session: AsyncSession, segment_id: int
) -> list[SegmentEffort]:
    candidates = (
        await session.execute(
            _CANDIDATE_TRACKS_FOR_SEGMENT, {"segment_id": segment_id, "buffer_m": BUFFER_M}
        )
    ).all()

    efforts = []
    for track_id, activity_id in candidates:
        effort = await _try_match(session, segment_id, track_id, activity_id)
        if effort is not None:
            efforts.append(effort)
    return efforts
