"""One-off backfill: populate tracks.times for tracks written before that
column existed.

Strava-sourced tracks only — their original files are still in
strava_data/, so this re-parses each and fills in the same times array
write_track_streams_laps() would have built at import time. There's
nothing to backfill for Garmin-sourced tracks yet (none exist), and going
forward both importers populate times from the start.

Usage:
    python -m app.importers.backfill_track_times --export-dir strava_data/export_2963950
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import pathlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session
from app.importers.db_writer import to_naive_utc
from app.importers.strava import SOURCE, _pick_parser
from app.models import Activity, Track


async def _backfill_one(
    session: AsyncSession,
    export_dir: pathlib.Path,
    activity_id: int,
    external_id: str,
    file_rel: str,
) -> bool:
    file_path = export_dir / file_rel
    parser = _pick_parser(file_path)
    if parser is None or not file_path.exists():
        print(f"SKIP {external_id}: no parseable file at {file_rel!r}")
        return False

    parsed = parser(file_path)
    gps_points = [p for p in parsed.points if p.lat is not None and p.lon is not None]
    if not gps_points or not all(p.time is not None for p in gps_points):
        print(f"SKIP {external_id}: source file has no fully-timed GPS points")
        return False

    times = [to_naive_utc(p.time) for p in gps_points]

    track = await session.scalar(select(Track).where(Track.activity_id == activity_id))
    if track is None:
        print(f"SKIP {external_id}: no tracks row for this activity")
        return False

    n_points = await session.scalar(select(func.ST_NPoints(Track.geom)).where(Track.id == track.id))
    if n_points != len(times):
        print(
            f"SKIP {external_id}: re-parsed {len(times)} points but geom has "
            f"{n_points} — source file may have changed since import, not safe to backfill"
        )
        return False

    track.times = times
    return True


async def run(export_dir: pathlib.Path) -> None:
    with open(export_dir / "activities.csv", newline="", encoding="utf-8") as fh:
        file_by_external_id = {
            row["ID de l'activité"]: (row.get("Nom du fichier") or "").strip()
            for row in csv.DictReader(fh)
        }

    async with async_session() as session:
        rows = (
            await session.execute(
                select(Activity.id, Activity.external_id)
                .join(Track, Track.activity_id == Activity.id)
                .where(Activity.source == SOURCE, Track.times.is_(None))
            )
        ).all()

    print(f"{len(rows)} tracks need backfilling")
    counts = {"filled": 0, "skipped": 0}

    for activity_id, external_id in rows:
        file_rel = file_by_external_id.get(external_id, "")
        if not file_rel:
            print(f"SKIP {external_id}: not found in activities.csv (or no file)")
            counts["skipped"] += 1
            continue

        async with async_session() as session:
            async with session.begin():
                ok = await _backfill_one(session, export_dir, activity_id, external_id, file_rel)
        counts["filled" if ok else "skipped"] += 1

    print(f"Done: {counts}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-dir", required=True, type=pathlib.Path)
    args = parser.parse_args()
    asyncio.run(run(args.export_dir))


if __name__ == "__main__":
    main()
