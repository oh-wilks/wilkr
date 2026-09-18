"""PostGIS spatial-buffer segment matching.

Two entry points, both built on the same core matcher:
- match_activity_against_segments(session, activity_id): a new activity
  just landed (Strava/Garmin import) — check it against every existing
  segment of the same sport.
- match_segment_against_activities(session, segment_id): a new segment
  was just created — check it against every existing activity of the same
  sport (the charter's "scan historical activities" requirement).

Algorithm (see docs/segments_roadmap.md's Phase H for the full diagnosis
this replaced — found via app/segments/diagnose*.py against real data,
not theorized):

1. Candidate filter — ST_DWithin on ::geography casts, using the existing
   GIST indexes. Cheap, coarse, just rules out tracks nowhere near the
   segment.
2. Pass detection — walk the TRACK's own points in chronological/
   point-index order (ST_DumpPoints), and group consecutive
   within-BUFFER_M points into "islands" (gaps-and-islands SQL pattern,
   tolerant of brief GPS-dropout gaps up to MAX_GAP_POINTS). Each island
   is one candidate pass through the segment. This is deliberately
   track-driven, not segment-driven: sampling the *segment* and asking
   "what's this sample's single nearest point on the whole track"
   (the previous approach) can only ever return one location per sample,
   so it structurally cannot represent a segment traversed more than once
   in one activity — an out-and-back, a repeated lap, a shuttle run. Two
   real, separate passes are separated in the track's own timeline, which
   only a track-driven walk has access to.
3. Per-pass verification — for each candidate island (padded by
   PAD_POINTS so the true segment start/end has track to locate onto),
   build an isolated sub-track from just that island's own points
   (ST_MakeLine in Python from ST_DumpPoints output, not a length-fraction
   ST_LineSubstring cut — GPS speed isn't constant, so a fraction-based
   cut would misplace the boundary on climbs vs. descents) and sample the
   segment against it: locate each sample (ST_LineLocatePoint), confirm
   proximity, confirm the located fractions are monotonically increasing
   (direction-sensitive) across at least MIN_MATCH_RATIO of samples. Since
   this sub-track physically doesn't contain any other pass's points, the
   ambiguity that motivated this whole redesign can't occur here — this
   step is unchanged in spirit from the original algorithm, just scoped
   to one geometrically-isolated pass instead of the whole track.
4. Elapsed time — convert the winning chain's first/last sample's located
   fraction to the nearest index in that pass's own (padded, sliced)
   times[], read the timestamp directly.
5. Record one SegmentEffort per verified pass whose time window doesn't
   overlap an already-recorded effort for that (segment, activity) pair —
   not one-per-(segment, activity) as before, since that's exactly the
   restriction that made a second real pass unrecordable even in
   principle.

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
MAX_GAP_POINTS = 30  # tolerate brief GPS dropout without splitting one real pass in
# two. Started at 15 (matched BUFFER_M numerically, but that was
# coincidence, not a derivation); real data (segment 22 "peña sola",
# activity 765) showed a genuine single forward pass fragmented into three
# islands by a 17-19 point gap where the track briefly drifted just past
# BUFFER_M near the segment's end. Both real distinct-pass gaps measured so
# far (una moss's two descents: ~1447 points; this activity's forward vs.
# backward pass: ~5506 points) are two-plus orders of magnitude larger, so
# there's wide margin to loosen this without risking a false merge.
PAD_POINTS = 10  # context around a candidate island so the true segment
# start/end (which may fall just outside the "within buffer" points
# themselves) has track to locate onto

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
# _SAMPLE_VERIFICATION above is no longer used by _try_match (superseded by
# the pass-scoped _SAMPLE_VERIFICATION_WKT below) but is kept — it's the
# exact query app/segments/diagnose*.py import to reproduce/compare against
# the whole-track behavior that motivated this file's Phase H redesign.

_CANDIDATE_PASSES = text(
    """
    WITH track_points AS (
        SELECT (dp).path[1] AS point_index, (dp).geom AS pt
        FROM tracks t, LATERAL ST_DumpPoints(ST_Force2D(t.geom)) AS dp
        WHERE t.id = :track_id
    ),
    distances AS (
        SELECT tp.point_index, ST_Distance(tp.pt::geography, s.geom::geography) AS dist_m
        FROM track_points tp, segments s
        WHERE s.id = :segment_id
    ),
    close AS (
        SELECT point_index, dist_m,
               point_index - LAG(point_index) OVER (ORDER BY point_index) AS gap
        FROM distances
        WHERE dist_m <= :buffer_m
    ),
    grouped AS (
        SELECT point_index, dist_m,
               SUM(CASE WHEN gap IS NULL OR gap > :max_gap THEN 1 ELSE 0 END)
                 OVER (ORDER BY point_index) AS pass_group
        FROM close
    )
    SELECT pass_group, MIN(point_index) AS start_idx, MAX(point_index) AS end_idx
    FROM grouped
    GROUP BY pass_group
    ORDER BY start_idx
    """
)

_PADDED_POINTS = text(
    """
    SELECT (dp).path[1] AS point_index, ST_X((dp).geom) AS lon, ST_Y((dp).geom) AS lat
    FROM tracks t, LATERAL ST_DumpPoints(ST_Force2D(t.geom)) AS dp
    WHERE t.id = :track_id
      AND (dp).path[1] BETWEEN :start_idx AND :end_idx
    ORDER BY point_index
    """
)

# Same shape as _SAMPLE_VERIFICATION, but against a WKT sub-track built in
# Python from one isolated candidate pass's own points (see _try_match),
# not a track_id lookup against the whole track.
_SAMPLE_VERIFICATION_WKT = text(
    """
    WITH samples AS (
        SELECT i, ST_LineInterpolatePoint(s.geom, i::float / :n_samples) AS sample_point
        FROM segments s, generate_series(0, :n_samples) AS i
        WHERE s.id = :segment_id
    ),
    sub_track AS (
        SELECT ST_GeomFromText(:sub_track_wkt, 4326) AS geom2d
    ),
    located AS (
        SELECT samples.i, samples.sample_point,
               ST_LineLocatePoint(sub_track.geom2d, samples.sample_point) AS track_fraction
        FROM samples, sub_track
    )
    SELECT located.i, located.track_fraction,
           ST_Distance(
               located.sample_point::geography,
               ST_LineInterpolatePoint(sub_track.geom2d, located.track_fraction)::geography
           ) AS distance_m
    FROM located, sub_track
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


async def _effort_overlaps_existing(
    session: AsyncSession,
    segment_id: int,
    activity_id: int,
    started_at: datetime.datetime,
    ended_at: datetime.datetime,
) -> bool:
    """Replaces the old _effort_exists (any row for this (segment, activity)
    pair) — that was exactly the restriction that made a second real pass
    unrecordable even in principle. This checks time-window overlap instead,
    so a genuinely repeated pass gets its own effort row, while re-matching
    the same pass (e.g. on rescan) doesn't create a duplicate."""
    existing = await session.execute(
        text(
            """
            SELECT 1 FROM segment_efforts
            WHERE segment_id = :segment_id AND activity_id = :activity_id
              AND achieved_at < :ended_at
              AND (achieved_at + (elapsed_time_s * INTERVAL '1 second')) > :started_at
            """
        ),
        {
            "segment_id": segment_id,
            "activity_id": activity_id,
            "started_at": started_at,
            "ended_at": ended_at,
        },
    )
    return existing.first() is not None


async def _try_match(
    session: AsyncSession, segment_id: int, track_id: int, activity_id: int
) -> list[SegmentEffort]:
    times = await session.scalar(
        text("SELECT times FROM tracks WHERE id = :track_id"), {"track_id": track_id}
    )
    if not times:
        return []

    n_track_points = await session.scalar(
        text("SELECT ST_NPoints(geom) FROM tracks WHERE id = :track_id"), {"track_id": track_id}
    )
    segment_length_m = await session.scalar(
        text("SELECT ST_Length(geom::geography) FROM segments WHERE id = :segment_id"),
        {"segment_id": segment_id},
    )

    passes = (
        await session.execute(
            _CANDIDATE_PASSES,
            {
                "segment_id": segment_id,
                "track_id": track_id,
                "buffer_m": BUFFER_M,
                "max_gap": MAX_GAP_POINTS,
            },
        )
    ).all()

    efforts: list[SegmentEffort] = []
    for p in passes:
        pad_start = max(1, p.start_idx - PAD_POINTS)
        pad_end = min(n_track_points, p.end_idx + PAD_POINTS)

        points = (
            await session.execute(
                _PADDED_POINTS,
                {"track_id": track_id, "start_idx": pad_start, "end_idx": pad_end},
            )
        ).all()
        if len(points) < 2:
            continue

        # ST_DumpPoints' path[1] is 1-indexed; tracks.times is a plain
        # 0-indexed list aligned 1:1 with the geometry's vertices in order
        # (see db_writer.py) — point_index - 1 for the correct element,
        # sliced to line up exactly with sub_track_wkt's own point sequence
        # so _evaluate_samples' fraction-to-time lookup resolves within
        # this pass's own timeline, not the whole track's.
        pass_times = [times[pt.point_index - 1] for pt in points]
        sub_track_wkt = "LINESTRING(" + ", ".join(f"{pt.lon} {pt.lat}" for pt in points) + ")"

        sample_rows = (
            await session.execute(
                _SAMPLE_VERIFICATION_WKT,
                {
                    "segment_id": segment_id,
                    "sub_track_wkt": sub_track_wkt,
                    "n_samples": N_SAMPLES,
                },
            )
        ).all()
        result = _evaluate_samples(sample_rows, pass_times, segment_length_m)
        if result is None:
            continue
        start_fraction, end_fraction = result

        started = _fraction_to_time(pass_times, start_fraction)
        ended = _fraction_to_time(pass_times, end_fraction)
        elapsed_s = int((ended - started).total_seconds())
        if elapsed_s <= 0:
            continue

        if await _effort_overlaps_existing(session, segment_id, activity_id, started, ended):
            continue

        effort = SegmentEffort(
            segment_id=segment_id,
            activity_id=activity_id,
            elapsed_time_s=elapsed_s,
            achieved_at=started,
        )
        session.add(effort)
        efforts.append(effort)

    return efforts


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
        efforts.extend(await _try_match(session, segment_id, track_id, activity_id))
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
        efforts.extend(await _try_match(session, segment_id, track_id, activity_id))
    return efforts


# --------------------------------------------------------------------------
# Near-duplicate detection, run at segment-creation time only (warn, not
# block — a near-duplicate is sometimes intentional, e.g. a deliberately
# adjusted climb boundary). Same sampling philosophy as the matcher above,
# but simpler: no time dimension, since we're comparing two static
# geometries rather than a segment against a GPS recording.
# --------------------------------------------------------------------------

SIMILARITY_BUFFER_M = 15
SIMILARITY_THRESHOLD = 0.8

_SIMILAR_CANDIDATES = text(
    """
    SELECT s.id, s.name
    FROM segments s
    WHERE s.sport_id = :sport_id
      AND (CAST(:exclude_id AS integer) IS NULL OR s.id != CAST(:exclude_id AS integer))
      AND ST_DWithin(s.geom::geography, ST_GeomFromText(:new_geom_wkt, 4326)::geography, :buffer_m)
    """
)

_SIMILAR_SAMPLES = text(
    """
    WITH samples AS (
        SELECT i, ST_LineInterpolatePoint(ST_GeomFromText(:new_geom_wkt, 4326), i::float / :n_samples) AS sample_point
        FROM generate_series(0, :n_samples) AS i
    )
    SELECT samples.i,
           ST_LineLocatePoint(s.geom, samples.sample_point) AS frac,
           ST_Distance(
               samples.sample_point::geography,
               ST_LineInterpolatePoint(s.geom, ST_LineLocatePoint(s.geom, samples.sample_point))::geography
           ) AS dist_m
    FROM samples, segments s
    WHERE s.id = :existing_id
    ORDER BY samples.i
    """
)


def _longest_nondecreasing(fractions: list[float]) -> int:
    """Length of the longest non-decreasing run, in original order.
    O(n^2) — trivial at N_SAMPLES+1 elements."""
    n = len(fractions)
    lengths = [1] * n
    for j in range(n):
        for k in range(j):
            if fractions[k] <= fractions[j] and lengths[k] + 1 > lengths[j]:
                lengths[j] = lengths[k] + 1
    return max(lengths) if n else 0


async def find_similar_segments(
    session: AsyncSession,
    new_geom_wkt: str,
    sport_id: int,
    exclude_segment_id: int | None = None,
) -> list[dict]:
    """Existing same-sport segments that substantially overlap the proposed
    new geometry in the same direction — a climb and its own descent on the
    same road share every point but not the direction, so they correctly
    don't flag each other. exclude_segment_id skips a segment against
    itself when checking an edit (it would otherwise always "match" its own
    pre-edit geometry at ~100%)."""
    candidates = (
        await session.execute(
            _SIMILAR_CANDIDATES,
            {
                "sport_id": sport_id,
                "new_geom_wkt": new_geom_wkt,
                "buffer_m": SIMILARITY_BUFFER_M,
                "exclude_id": exclude_segment_id,
            },
        )
    ).all()

    similar = []
    for existing_id, name in candidates:
        rows = (
            await session.execute(
                _SIMILAR_SAMPLES,
                {
                    "new_geom_wkt": new_geom_wkt,
                    "existing_id": existing_id,
                    "n_samples": N_SAMPLES,
                },
            )
        ).all()
        required = math.ceil(SIMILARITY_THRESHOLD * len(rows))
        fractions = [frac for _, frac, dist in rows if dist is not None and dist <= SIMILARITY_BUFFER_M]
        if len(fractions) < required:
            continue
        if _longest_nondecreasing(fractions) < required:
            continue
        similar.append({"id": existing_id, "name": name})
    return similar
