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
from app.importers.parsers.common import ParsedActivity, TrackPoint
from app.importers.parsers.fit import parse_fit
from app.importers.parsers.gpx import parse_gpx
from app.importers.parsers.tcx import parse_tcx
from app.importers.sport_mapping import resolve_sport
from app.models import (
    Activity,
    ActivityLap,
    Equipment,
    ImportEvent,
    Sport,
    Stream,
    Track,
    User,
    UserPreferences,
    equipment_sports,
)
from geoalchemy2.elements import WKTElement

SOURCE = "strava_import"

_FRENCH_MONTHS = {
    "janv.": 1, "févr.": 2, "mars": 3, "avr.": 4, "mai": 5, "juin": 6,
    "juil.": 7, "août": 8, "sept.": 9, "oct.": 10, "nov.": 11, "déc.": 12,
}

STREAM_EXTRACTORS = {
    "heart_rate": lambda p: p.hr,
    "elevation": lambda p: p.ele,
    "speed": lambda p: p.speed,
    "cadence": lambda p: p.cadence,
    "power": lambda p: p.power,
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


def _to_naive_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


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


def _points_to_linestring_z(points: list[TrackPoint]) -> WKTElement:
    coords = ", ".join(f"{p.lon} {p.lat} {p.ele or 0}" for p in points)
    return WKTElement(f"LINESTRING Z({coords})", srid=4326)


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
        first_timed_point = next((p for p in parsed.points if p.time is not None), None)
        if first_timed_point is not None:
            started_at = _to_naive_utc(first_timed_point.time)

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
        gps_points = [p for p in parsed.points if p.lat is not None and p.lon is not None]
        if len(gps_points) >= 2:
            session.add(
                Track(activity_id=activity.id, geom=_points_to_linestring_z(gps_points))
            )

        for stream_type, extractor in STREAM_EXTRACTORS.items():
            series = [
                {"t": _to_naive_utc(p.time).isoformat(), "v": extractor(p)}
                for p in parsed.points
                if p.time is not None and extractor(p) is not None
            ]
            if series:
                session.add(Stream(activity_id=activity.id, type=stream_type, data=series))

        for lap in parsed.laps:
            session.add(
                ActivityLap(
                    activity_id=activity.id,
                    lap_index=lap.lap_index,
                    lap_type="active",
                    started_at=_to_naive_utc(lap.started_at),
                    elapsed_time_s=lap.elapsed_time_s,
                    distance_m=lap.distance_m,
                    elevation_change_m=lap.elevation_change_m,
                    avg_speed_mps=lap.avg_speed_mps,
                    max_speed_mps=lap.max_speed_mps,
                )
            )

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-dir", required=True, type=pathlib.Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(run(args.export_dir, args.limit))


if __name__ == "__main__":
    main()
