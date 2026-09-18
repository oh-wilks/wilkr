"""Standalone test of the candidate-pass-detection + per-pass verification
now live in matching.py's _try_match (Phase H, docs/segments_roadmap.md) —
imports the actual production queries/constants rather than keeping a
parallel copy, so this always reflects exactly what the real matcher does,
not a near-duplicate that could drift from it.

Walks the track's own dumped points in chronological/point-index order,
computes each one's distance to the segment, and groups consecutive
within-buffer points into "islands" (gaps-and-islands SQL pattern,
tolerant of brief GPS-dropout gaps) — each island is a candidate pass. For
each one, builds an isolated sub-track and re-runs the same chain/
monotonicity check the original algorithm used, scoped to just that pass.

Usage (inside the api container):
    python -m app.segments.diagnose_passes --segment-id 26 --activity-id 2
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.db.session import async_session
from app.segments.matching import (
    BUFFER_M,
    MAX_GAP_POINTS,
    N_SAMPLES,
    PAD_POINTS,
    _CANDIDATE_PASSES,
    _PADDED_POINTS,
    _SAMPLE_VERIFICATION_WKT,
    _evaluate_samples,
    _fraction_to_time,
)


async def diagnose_passes(
    segment_id: int,
    track_id: int | None,
    activity_id: int | None,
    buffer_m: float,
    verbose: bool = False,
) -> None:
    async with async_session() as session:
        if track_id is None:
            track_id = await session.scalar(
                text("SELECT id FROM tracks WHERE activity_id = :activity_id"),
                {"activity_id": activity_id},
            )
            if track_id is None:
                print(f"No track for activity {activity_id}")
                return

        n_track_points = await session.scalar(
            text("SELECT ST_NPoints(geom) FROM tracks WHERE id = :track_id"), {"track_id": track_id}
        )
        print(f"track {track_id} has {n_track_points} points, buffer={buffer_m}m, max_gap={MAX_GAP_POINTS}pts\n")

        passes = (
            await session.execute(
                _CANDIDATE_PASSES,
                {
                    "segment_id": segment_id,
                    "track_id": track_id,
                    "buffer_m": buffer_m,
                    "max_gap": MAX_GAP_POINTS,
                },
            )
        ).all()

        if not passes:
            print("No candidate passes found.")
            return

        print(f"{len(passes)} candidate pass(es):")
        for p in passes:
            print(f"  pass {p.pass_group}: points [{p.start_idx}..{p.end_idx}]")

        all_times = await session.scalar(
            text("SELECT times FROM tracks WHERE id = :track_id"), {"track_id": track_id}
        )
        segment_length_m = await session.scalar(
            text("SELECT ST_Length(geom::geography) FROM segments WHERE id = :segment_id"),
            {"segment_id": segment_id},
        )

        print("\n=== per-pass verification (isolated sub-track, matching.py's real logic) ===")
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
                print(f"  pass {p.pass_group}: too few points after padding, skipping")
                continue

            pass_times = [all_times[pt.point_index - 1] for pt in points]
            sub_track_wkt = "LINESTRING(" + ", ".join(f"{pt.lon} {pt.lat}" for pt in points) + ")"

            sample_rows = (
                await session.execute(
                    _SAMPLE_VERIFICATION_WKT,
                    {"segment_id": segment_id, "sub_track_wkt": sub_track_wkt, "n_samples": N_SAMPLES},
                )
            ).all()
            result = _evaluate_samples(sample_rows, pass_times, segment_length_m)

            if verbose:
                print(f"  pass {p.pass_group} raw samples (padded points [{pad_start}..{pad_end}]):")
                print(f"  {'i':>3}  {'seg_frac':>10}  {'dist_m':>8}  {'pass':>4}")
                for i, frac, dist in sample_rows:
                    passed = dist is not None and dist <= buffer_m
                    dist_str = f"{dist:.1f}" if dist is not None else "n/a"
                    print(f"  {i:>3}  {frac:>10.4f}  {dist_str:>8}  {'Y' if passed else '.':>4}")

            if result is None:
                print(f"  pass {p.pass_group}: FAILED verification (isolated sub-track didn't clear the chain check)")
                continue

            start_frac, end_frac = result
            started_at = _fraction_to_time(pass_times, start_frac)
            ended_at = _fraction_to_time(pass_times, end_frac)
            elapsed_s = (ended_at - started_at).total_seconds()
            print(
                f"  pass {p.pass_group}: VERIFIED — elapsed={elapsed_s:.0f}s  "
                f"started_at={started_at}  ended_at={ended_at}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segment-id", type=int, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--track-id", type=int)
    group.add_argument("--activity-id", type=int)
    parser.add_argument("--buffer-m", type=float, default=BUFFER_M)
    parser.add_argument("--verbose", action="store_true", help="print raw per-sample fraction/distance table")
    args = parser.parse_args()
    asyncio.run(
        diagnose_passes(args.segment_id, args.track_id, args.activity_id, args.buffer_m, args.verbose)
    )


if __name__ == "__main__":
    main()
