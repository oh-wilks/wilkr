"""Diagnose why a geographically-plausible (segment, activity) pair has no
recorded effort — before touching any of the matcher's constants.

Third iteration of this script — the first ran 3 sequential DB queries per
candidate pair (slow at 826 activities); the second replaced the join with
a LATERAL to force GIST index usage, on the theory that a plain multi-table
join with no equality condition doesn't give Postgres's planner enough of
a hint — but that still hung. Rather than keep guessing at query shapes,
this version does the one thing we actually have *proof* is fast at this
data scale: it reuses matching.py's real _CANDIDATE_TRACKS_FOR_SEGMENT
query almost verbatim (a literal bound :segment_id, matching one row by
primary key — not a LATERAL-correlated reference to an outer row, which
may be why the planner handled it differently), looped over segments in
Python. This is the exact shape match_segment_against_activities already
runs every time a segment is created, just without the sport_id filter —
so if THIS hangs too, the problem isn't query-shape cleverness, it's
something more fundamental (data volume, geometry size, missing index in
practice) worth knowing on its own.

Usage (inside the api container):
    python -m app.segments.diagnose                 # every segment
    python -m app.segments.diagnose --segment-id 5   # just one
"""

from __future__ import annotations

import argparse
import asyncio
import time

from sqlalchemy import text

from app.db.session import async_session
from app.segments.matching import (
    BUFFER_M,
    MIN_MATCH_RATIO,
    N_SAMPLES,
    _SAMPLE_VERIFICATION,
    _fraction_to_time,
    _longest_plausible_run,
)

_SEGMENTS = text(
    """
    SELECT id, name, sport_id, ST_Length(geom::geography) AS length_m
    FROM segments
    WHERE (CAST(:segment_id AS integer) IS NULL OR id = CAST(:segment_id AS integer))
    ORDER BY id
    """
)

# Same shape as matching.py's _CANDIDATE_TRACKS_FOR_SEGMENT: segments joined
# via a literal :segment_id parameter (pins to exactly one row by primary
# key), tracks as the driving table — not sport-filtered here, so this
# covers both the mismatch and near-miss cases in one pass per segment.
_NEARBY_TRACKS = text(
    """
    SELECT t.id AS track_id, t.times, a.id AS activity_id, a.name AS activity_name,
           a.sport_id AS activity_sport
    FROM tracks t
    JOIN activities a ON a.id = t.activity_id
    JOIN segments s ON s.id = :segment_id
    WHERE t.times IS NOT NULL
      AND ST_DWithin(s.geom::geography, t.geom::geography, :buffer_m)
      AND NOT EXISTS (
        SELECT 1 FROM segment_efforts se
        WHERE se.segment_id = s.id AND se.activity_id = a.id
      )
    """
)


async def diagnose(segment_id: int | None = None) -> None:
    async with async_session() as session:
        segments = (
            await session.execute(_SEGMENTS, {"segment_id": segment_id})
        ).all()
        print(f"{len(segments)} segment(s) to check\n")

        for seg in segments:
            t0 = time.monotonic()
            tracks = (
                await session.execute(
                    _NEARBY_TRACKS,
                    {"segment_id": seg.id, "buffer_m": BUFFER_M},
                )
            ).all()
            elapsed = time.monotonic() - t0
            print(
                f"segment={seg.id} '{seg.name}' (sport {seg.sport_id}): "
                f"{len(tracks)} nearby track(s) without an effort  [{elapsed:.2f}s]"
            )

            for tr in tracks:
                if tr.activity_sport != seg.sport_id:
                    print(
                        f"  -> activity={tr.activity_id} '{tr.activity_name}' "
                        f"sport_mismatch({seg.sport_id}->{tr.activity_sport})"
                    )
                    continue

                sample_rows = (
                    await session.execute(
                        _SAMPLE_VERIFICATION,
                        {"segment_id": seg.id, "track_id": tr.track_id, "n_samples": N_SAMPLES},
                    )
                ).all()
                distances = [dist for _, _, dist in sample_rows]
                passing = [
                    (i, frac) for i, frac, dist in sample_rows if dist is not None and dist <= BUFFER_M
                ]
                total = len(sample_rows)
                pass_rate = len(passing) / total if total else 0.0

                indices = [i for i, _ in passing]
                fractions = [frac for _, frac in passing]
                sample_times = [_fraction_to_time(tr.times, frac) for frac in fractions]
                chain = _longest_plausible_run(indices, fractions, sample_times, N_SAMPLES, seg.length_m)
                chain_ratio = len(chain) / total if total else 0.0

                worst = max((d for d in distances if d is not None), default=None)
                worst_str = f"{worst:.1f}m" if worst is not None else "n/a"

                reasons = []
                if pass_rate < MIN_MATCH_RATIO:
                    reasons.append(f"pass_rate={pass_rate:.0%}<{MIN_MATCH_RATIO:.0%}")
                if chain_ratio < MIN_MATCH_RATIO:
                    reasons.append(f"chain_ratio={chain_ratio:.0%}<{MIN_MATCH_RATIO:.0%}")
                reason_str = ", ".join(reasons) if reasons else "PASSES ALL CHECKS (unexpected — investigate)"

                print(
                    f"  -> activity={tr.activity_id} '{tr.activity_name}'  "
                    f"worst_sample={worst_str}  {reason_str}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segment-id", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(diagnose(args.segment_id))


if __name__ == "__main__":
    main()
