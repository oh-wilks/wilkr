"""Deep-dive diagnostic for one specific activity: for every same-sport
segment geographically near its track, print the FULL raw sample table
(track fraction + distance per sample, and which samples the matcher's
longest-plausible-run chain actually picked) — not just a summary
pass/chain percentage. Built to inspect a known, concrete case (an
activity that rode a segment's road twice in one recording — an
out-and-back, two laps of the same climb) directly, rather than inferring
the mechanism from aggregate stats the way diagnose.py's summary output
has to.

Mirrors matching.py's own _CANDIDATE_SEGMENTS_FOR_TRACK shape exactly —
literal :track_id bound parameter (pins to one track by primary key),
segments as the joined table — same proven-fast shape diagnose.py ended
up using, just driven from the activity side instead of the segment side.

Usage (inside the api container):
    python -m app.segments.diagnose_activity --activity-id 765
    python -m app.segments.diagnose_activity --activity-id 765 --segment-id 22
"""

from __future__ import annotations

import argparse
import asyncio

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

_CANDIDATES_FOR_TRACK = text(
    """
    SELECT s.id AS segment_id, s.name AS segment_name,
           ST_Length(s.geom::geography) AS length_m
    FROM segments s
    JOIN tracks t ON t.id = :track_id
    JOIN activities a ON a.id = t.activity_id
    WHERE s.sport_id = a.sport_id
      AND (CAST(:segment_id AS integer) IS NULL OR s.id = CAST(:segment_id AS integer))
      AND ST_DWithin(s.geom::geography, t.geom::geography, :buffer_m)
    ORDER BY s.id
    """
)


async def diagnose_activity(activity_id: int, segment_id: int | None) -> None:
    async with async_session() as session:
        track_row = (
            await session.execute(
                text("SELECT id, times FROM tracks WHERE activity_id = :activity_id"),
                {"activity_id": activity_id},
            )
        ).first()
        if track_row is None:
            print(f"No track for activity {activity_id}")
            return
        track_id, times = track_row
        if not times:
            print(f"Track {track_id} has no times[] — can't compute elapsed time")
            return

        existing = (
            await session.execute(
                text(
                    """
                    SELECT se.segment_id, s.name, se.elapsed_time_s, se.achieved_at
                    FROM segment_efforts se JOIN segments s ON s.id = se.segment_id
                    WHERE se.activity_id = :activity_id
                    ORDER BY se.achieved_at
                    """
                ),
                {"activity_id": activity_id},
            )
        ).all()
        print(f"Activity {activity_id}, track {track_id}: {len(existing)} existing effort(s) already recorded")
        for row in existing:
            print(f"  segment={row[0]} '{row[1]}'  {row[2]}s  achieved_at={row[3]}")
        print()

        candidates = (
            await session.execute(
                _CANDIDATES_FOR_TRACK,
                {"track_id": track_id, "segment_id": segment_id, "buffer_m": BUFFER_M},
            )
        ).all()
        print(f"{len(candidates)} candidate segment(s) geographically near this track\n")

        for seg in candidates:
            rows = (
                await session.execute(
                    _SAMPLE_VERIFICATION,
                    {"segment_id": seg.segment_id, "track_id": track_id, "n_samples": N_SAMPLES},
                )
            ).all()
            passing = [(i, frac) for i, frac, dist in rows if dist is not None and dist <= BUFFER_M]
            total = len(rows)
            pass_rate = len(passing) / total if total else 0.0

            indices = [i for i, _ in passing]
            fractions = [frac for _, frac in passing]
            sample_times = [_fraction_to_time(times, frac) for frac in fractions]
            chain = _longest_plausible_run(indices, fractions, sample_times, N_SAMPLES, seg.length_m)
            chain_ratio = len(chain) / total if total else 0.0
            chain_i_values = {indices[pos] for pos in chain}

            verdict = "MATCH" if pass_rate >= MIN_MATCH_RATIO and chain_ratio >= MIN_MATCH_RATIO else "no match"
            print(
                f"=== segment={seg.segment_id} '{seg.segment_name}'  "
                f"pass_rate={pass_rate:.0%}  chain_ratio={chain_ratio:.0%}  ({verdict}) ==="
            )
            print(f"{'i':>3}  {'track_frac':>10}  {'dist_m':>8}  {'pass':>4}  {'chain':>5}")
            for i, frac, dist in rows:
                passed = dist is not None and dist <= BUFFER_M
                in_chain = i in chain_i_values
                dist_str = f"{dist:.1f}" if dist is not None else "n/a"
                print(f"{i:>3}  {frac:>10.4f}  {dist_str:>8}  {'Y' if passed else '.':>4}  {'*' if in_chain else '':>5}")
            print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activity-id", type=int, required=True)
    parser.add_argument("--segment-id", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(diagnose_activity(args.activity_id, args.segment_id))


if __name__ == "__main__":
    main()
