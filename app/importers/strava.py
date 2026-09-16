"""One-time bulk importer for a Strava export archive.

Usage (inside the api container, with strava_data/ bind-mounted via
docker-compose.override.yml):

    python -m app.importers.strava --export-dir strava_data/export_2963950
    python -m app.importers.strava --export-dir strava_data/export_2963950 --limit 5

Idempotent: re-running skips any activity already present for
(source='strava_import', external_id), logging it as a 'duplicate'
import_event instead of touching the activities table.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime
import pathlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session
from app.importers.db_writer import first_timed_point, to_naive_utc, write_track_streams_laps
from app.importers.parsers.common import ParsedActivity
from app.importers.parsers.fit import parse_fit
from app.importers.parsers.gpx import parse_gpx
from app.importers.parsers.tcx import parse_tcx
from app.importers.sport_mapping import resolve_sport
from app.segments.matching import match_activity_against_segments
from app.models import (
    Activity,
    Equipment,
    ImportEvent,
    Sport,
    User,
    UserPreferences,
    equipment_sports,
)

SOURCE = "strava_import"

_FRENCH_MONTHS = {
    "janv.": 1, "févr.": 2, "mars": 3, "avr.": 4, "mai": 5, "juin": 6,
    "juil.": 7, "août": 8, "sept.": 9, "oct.": 10, "nov.": 11, "déc.": 12,
}


# --------------------------------------------------------------------------
# small parsing helpers
# --------------------------------------------------------------------------

def _to_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


def _to_int(value: str | None) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _parse_strava_date(value: str) -> datetime.datetime:
    # "28 août 2026, 17:33:44" -> naive local datetime (export display
    # locale, no timezone info available — this is the fallback used only
    # when a GPS/sensor file's own absolute UTC timestamp isn't available).
    date_part, time_part = value.split(",")
    day_str, month_str, year_str = date_part.strip().split(" ")
    hour, minute, second = (int(x) for x in time_part.strip().split(":"))
    return datetime.datetime(
        int(year_str), _FRENCH_MONTHS[month_str], int(day_str), hour, minute, second
    )


def _merge_description(row: dict[str, str]) -> str | None:
    description = (row.get("Description de l'activité") or "").strip()
    private_note = (row.get("Note privée sur les activités") or "").strip()
    parts = [p for p in (description, private_note) if p]
    return "\n\n".join(parts) if parts else None


def _pick_parser(path: pathlib.Path):
    suffixes = "".join(path.suffixes).lower()
    if "fit" in suffixes:
        return parse_fit
    if "gpx" in suffixes:
        return parse_gpx
    if "tcx" in suffixes:
        return parse_tcx
    return None


# --------------------------------------------------------------------------
# bootstrap: the one user + seeded equipment, from the archive's own CSVs
# --------------------------------------------------------------------------

async def _bootstrap_user(session: AsyncSession, export_dir: pathlib.Path) -> int:
    with open(export_dir / "profile.csv", newline="", encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))

    email = row["Adresse e-mail"].strip()
    display_name = f"{row['Prénom'].strip()} {row['Nom'].strip()}".strip()
    weight_kg = _to_float(row.get("Poids"))

    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            username=email.split("@")[0],
            email=email,
            display_name=display_name,
            weight_kg=weight_kg,
            timezone="UTC",
        )
        session.add(user)
        await session.flush()
    else:
        user.display_name = display_name
        if weight_kg is not None:
            user.weight_kg = weight_kg

    prefs = await session.scalar(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    )
    if prefs is None:
        session.add(UserPreferences(user_id=user.id))

    await session.commit()
    return user.id


async def _seed_equipment(
    session: AsyncSession, export_dir: pathlib.Path, user_id: int
) -> dict[str, int]:
    cache: dict[str, int] = {}
    for filename, equipment_type in (("bikes.csv", "bike"), ("shoes.csv", "shoe")):
        path = export_dir / filename
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name_key = next(iter(row))  # "Nom du vélo" / "Nom des chaussures"
                name = row.get(name_key, "").strip()
                if not name:
                    continue
                brand_key = next(k for k in row if "Marque" in k)
                model_key = next(k for k in row if "Modèle" in k)
                brand = row.get(brand_key, "").strip() or None
                model = row.get(model_key, "").strip() or None

                equipment = await session.scalar(
                    select(Equipment).where(
                        Equipment.user_id == user_id, Equipment.name == name
                    )
                )
                if equipment is None:
                    equipment = Equipment(
                        user_id=user_id,
                        name=name,
                        type=equipment_type,
                        brand=brand,
                        model=model,
                    )
                    session.add(equipment)
                    await session.flush()
                cache[name] = equipment.id

    await session.commit()
    return cache


# --------------------------------------------------------------------------
# per-activity get-or-create helpers
# --------------------------------------------------------------------------

async def _get_or_create_sport(
    session: AsyncSession, cache: dict[str, int], name: str, category: str
) -> int:
    if name in cache:
        return cache[name]
    sport = await session.scalar(select(Sport).where(Sport.name == name))
    if sport is None:
        sport = Sport(name=name, category=category)
        session.add(sport)
        await session.flush()
    cache[name] = sport.id
    return sport.id


async def _ensure_equipment_sport_link(
    session: AsyncSession, equipment_id: int, sport_id: int
) -> None:
    exists = await session.scalar(
        select(equipment_sports.c.equipment_id).where(
            equipment_sports.c.equipment_id == equipment_id,
            equipment_sports.c.sport_id == sport_id,
        )
    )
    if exists is None:
        await session.execute(
            equipment_sports.insert().values(equipment_id=equipment_id, sport_id=sport_id)
        )


# --------------------------------------------------------------------------
# per-activity import
# --------------------------------------------------------------------------

async def _import_one_activity(
    session: AsyncSession,
    export_dir: pathlib.Path,
    user_id: int,
    sport_cache: dict[str, int],
    equipment_cache: dict[str, int],
    row: dict[str, str],
) -> int:
    sport_name, category = resolve_sport(row["Type d'activité"])
    sport_id = await _get_or_create_sport(session, sport_cache, sport_name, category)

    gear_name = (row.get("Matériel utilisé pour l'activité") or "").strip()
    equipment_id = equipment_cache.get(gear_name) if gear_name else None
    if equipment_id is not None:
        await _ensure_equipment_sport_link(session, equipment_id, sport_id)

    file_rel = (row.get("Nom du fichier") or "").strip()
    parsed: ParsedActivity | None = None
    if file_rel:
        file_path = export_dir / file_rel
        parser = _pick_parser(file_path)
        if parser is not None and file_path.exists():
            parsed = parser(file_path)

    started_at = _parse_strava_date(row["Date de l'activité"])
    if parsed:
        # Some GPX exports carry position data with no per-point <time> at
        # all (a pure route trace) — fall back to the CSV date rather than
        # crash on the first (untimed) point.
        point = first_timed_point(parsed)
        if point is not None:
            started_at = to_naive_utc(point.time)

    elapsed_time_s = _to_int(row.get("Temps écoulé")) or 0
    moving_time_s = _to_int(row.get("Durée de déplacement")) or elapsed_time_s

    activity = Activity(
        user_id=user_id,
        sport_id=sport_id,
        equipment_id=equipment_id,
        source=SOURCE,
        external_id=row["ID de l'activité"],
        name=row["Nom de l'activité"],
        description=_merge_description(row),
        started_at=started_at,
        moving_time_s=moving_time_s,
        elapsed_time_s=elapsed_time_s,
        distance_m=_to_float(row.get("Distance")) or 0.0,
        elevation_gain_m=_to_float(row.get("Dénivelé positif")),
        avg_hr=_to_int(row.get("Fréquence cardiaque moyenne")),
        max_hr=_to_int(row.get("Fréquence cardiaque max.")),
        calories_kcal=_to_int(row.get("Calories")),
    )
    session.add(activity)
    await session.flush()

    if parsed:
        write_track_streams_laps(session, activity.id, parsed)

    return activity.id


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

async def run(export_dir: pathlib.Path, limit: int | None) -> None:
    async with async_session() as session:
        user_id = await _bootstrap_user(session, export_dir)

    async with async_session() as session:
        equipment_cache = await _seed_equipment(session, export_dir, user_id)

    sport_cache: dict[str, int] = {}

    with open(export_dir / "activities.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if limit is not None:
        rows = rows[:limit]

    counts = {"success": 0, "failed": 0, "duplicate": 0}
    new_activity_ids: list[int] = []

    for row in rows:
        external_id = row["ID de l'activité"]

        async with async_session() as session:
            existing_id = await session.scalar(
                select(Activity.id).where(
                    Activity.source == SOURCE, Activity.external_id == external_id
                )
            )
            if existing_id is not None:
                session.add(
                    ImportEvent(
                        user_id=user_id,
                        source=SOURCE,
                        status="duplicate",
                        activity_id=existing_id,
                    )
                )
                await session.commit()
                counts["duplicate"] += 1
                continue

        try:
            async with async_session() as session:
                async with session.begin():
                    activity_id = await _import_one_activity(
                        session, export_dir, user_id, sport_cache, equipment_cache, row
                    )
            async with async_session() as session:
                session.add(
                    ImportEvent(
                        user_id=user_id,
                        source=SOURCE,
                        status="success",
                        activity_id=activity_id,
                    )
                )
                await session.commit()
            counts["success"] += 1
            new_activity_ids.append(activity_id)
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the run
            async with async_session() as session:
                session.add(
                    ImportEvent(
                        user_id=user_id,
                        source=SOURCE,
                        status="failed",
                        error_message=str(exc)[:1000],
                    )
                )
                await session.commit()
            counts["failed"] += 1
            print(f"FAILED {external_id}: {exc}")

    print(f"Done: {counts}")

    # Segment matching runs as a separate pass after the import loop, not
    # inside each activity's own transaction — keeps the already-verified
    # import path untouched, and matches the charter's "scan new
    # activities" wording without slowing down bulk import.
    segment_matches = 0
    for activity_id in new_activity_ids:
        try:
            async with async_session() as session:
                async with session.begin():
                    efforts = await match_activity_against_segments(session, activity_id)
                    segment_matches += len(efforts)
        except Exception as exc:  # noqa: BLE001 — one bad match must not stop the rest
            print(f"Segment matching failed for activity {activity_id}: {exc}")

    if new_activity_ids:
        print(f"Segment matches: {segment_matches}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-dir", required=True, type=pathlib.Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(run(args.export_dir, args.limit))


if __name__ == "__main__":
    main()
