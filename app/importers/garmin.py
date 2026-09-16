"""Ongoing Garmin Connect sync job.

Usage (inside the garmin-sync container):

    python -m app.importers.garmin --login-only   # interactive, TTY required —
                                                     run this by hand once, before
                                                     ever starting --loop
    python -m app.importers.garmin --once          # single sync pass
    python -m app.importers.garmin --loop           # scheduled service entrypoint
                                                       (GARMIN_SYNC_INTERVAL_S, default 3600)

Mirrors app/importers/strava.py's per-activity transaction + import_events
pattern almost exactly (see app/importers/db_writer.py for the shared
tracks/streams/activity_laps writing code both use). What's different:
auth/token persistence via garminconnect's own tokenstore resume+refresh,
get_activities_by_date + download_activity (which returns a ZIP, not a raw
FIT file — see _extract_fit_bytes) in place of reading a local export
archive, and garmin_sync_state for "since when" tracking instead of a CSV
file to walk once.

Idempotent the same way strava.py is: re-running (or an overlapping window
from the lookback buffer) skips any activity already present for
(source='garmin_sync', external_id), logging it as a 'duplicate'
import_event.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import io
import os
import pathlib
import tempfile
import zipfile

from garminconnect import Garmin
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session
from app.importers.db_writer import to_naive_utc, write_track_streams_laps
from app.importers.garmin_sport_mapping import resolve_sport
from app.importers.parsers.fit import parse_fit
from app.models import Activity, GarminSyncState, ImportEvent, Sport, User

SOURCE = "garmin_sync"

LOOKBACK_BUFFER = datetime.timedelta(days=3)
INITIAL_BACKFILL = datetime.timedelta(days=7)

TOKENSTORE_DEFAULT = os.environ.get("GARMIN_TOKENSTORE", "/data/garmin")

# A legitimate single-activity FIT file (even a multi-day ultra/expedition)
# is at most a few MB. 50MB is a generous ceiling that still catches a
# zip-bomb or corrupt response before it's decompressed into memory.
MAX_FIT_SIZE_BYTES = 50 * 1024 * 1024


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _num_int(value) -> int | None:
    return int(value) if value is not None else None


def _num_float(value) -> float | None:
    return float(value) if value is not None else None


def _scrub_secrets(text: str) -> str:
    """Defense in depth for error messages that get stored in the DB and
    rendered on the (unauthenticated) web viewer — don't rely solely on
    garminconnect's own exception-sanitizing to keep GARMIN_EMAIL/PASSWORD
    out of them."""
    for value in (os.environ.get("GARMIN_EMAIL"), os.environ.get("GARMIN_PASSWORD")):
        if value:
            text = text.replace(value, "***")
    return text


def _extract_fit_bytes(zip_bytes: bytes) -> bytes:
    """download_activity(..., ORIGINAL) returns the raw bytes of a ZIP
    archive — Garmin always wraps the native FIT export this way, even for
    a single file, and the library does not unzip it for you."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        fit_names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
        if not fit_names:
            raise ValueError(
                f"no .fit file found in Garmin's ORIGINAL download "
                f"(zip members: {zf.namelist()})"
            )
        info = zf.getinfo(fit_names[0])
        if info.file_size > MAX_FIT_SIZE_BYTES:
            raise ValueError(
                f"refusing to extract {fit_names[0]}: {info.file_size} bytes "
                f"exceeds the {MAX_FIT_SIZE_BYTES}-byte sanity cap"
            )
        return zf.read(fit_names[0])


def _parse_start_time_local(raw: dict, fallback: datetime.datetime) -> datetime.datetime:
    value = raw.get("startTimeLocal")
    if not value:
        return fallback
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return fallback


def _fail_fast_mfa() -> str:
    raise RuntimeError(
        "Garmin requested an MFA code during an unattended run — the "
        "persisted session must have gone stale or been invalidated. Run "
        "`python -m app.importers.garmin --login-only` interactively (needs "
        "a TTY) to re-authenticate, then restart the scheduled service."
    )


def _build_client(interactive: bool) -> Garmin:
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    prompt_mfa = (lambda: input("Garmin MFA code: ")) if interactive else _fail_fast_mfa
    client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    client.login(TOKENSTORE_DEFAULT)
    return client


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

async def _get_the_user(session: AsyncSession) -> int:
    user_id = await session.scalar(select(User.id).limit(1))
    if user_id is None:
        raise RuntimeError(
            "No user found. Garmin sync has no bootstrap of its own (unlike "
            "the Strava importer, which creates the one user from "
            "profile.csv) — run the Strava importer first, or create a "
            "user another way, before running Garmin sync."
        )
    return user_id


async def _get_or_create_sync_state(session: AsyncSession) -> GarminSyncState:
    state = await session.scalar(select(GarminSyncState).limit(1))
    if state is None:
        state = GarminSyncState()
        session.add(state)
        await session.flush()
    return state


async def _record_run_result(
    run_started_at: datetime.datetime,
    status: str,
    error_message: str | None,
    advance_cursor: bool,
) -> None:
    async with async_session() as session:
        state = await _get_or_create_sync_state(session)
        if advance_cursor:
            state.last_synced_at = run_started_at
        state.last_run_status = status
        state.last_error_message = error_message
        await session.commit()


# --------------------------------------------------------------------------
# per-activity get-or-create (sport — no equipment concept from Garmin's API)
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


# --------------------------------------------------------------------------
# per-activity import
# --------------------------------------------------------------------------

async def _import_one_activity(
    session: AsyncSession,
    client: Garmin,
    user_id: int,
    sport_cache: dict[str, int],
    raw: dict,
    run_started_at: datetime.datetime,
) -> int:
    external_id = str(raw["activityId"])
    type_key = (raw.get("activityType") or {}).get("typeKey", "")
    sport_name, category = resolve_sport(type_key)
    sport_id = await _get_or_create_sport(session, sport_cache, sport_name, category)

    zip_bytes = client.download_activity(
        external_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
    )
    fit_bytes = _extract_fit_bytes(zip_bytes)

    with tempfile.NamedTemporaryFile(suffix=".fit") as tmp:
        tmp.write(fit_bytes)
        tmp.flush()
        parsed = parse_fit(pathlib.Path(tmp.name))

    started_at = _parse_start_time_local(raw, fallback=run_started_at)
    if parsed.points:
        point = next((p for p in parsed.points if p.time is not None), None)
        if point is not None:
            started_at = to_naive_utc(point.time)

    moving_time_s = _num_int(raw.get("duration")) or 0
    elapsed_time_s = _num_int(raw.get("elapsedDuration")) or moving_time_s

    activity = Activity(
        user_id=user_id,
        sport_id=sport_id,
        equipment_id=None,  # Garmin's activity list has no equipment concept in this API
        source=SOURCE,
        external_id=external_id,
        name=raw.get("activityName") or "Garmin Activity",
        description=None,
        started_at=started_at,
        moving_time_s=moving_time_s,
        elapsed_time_s=elapsed_time_s,
        distance_m=_num_float(raw.get("distance")) or 0.0,
        elevation_gain_m=_num_float(raw.get("elevationGain")),
        avg_hr=_num_int(raw.get("averageHR")),
        max_hr=_num_int(raw.get("maxHR")),
        calories_kcal=_num_int(raw.get("calories")),
    )
    session.add(activity)
    await session.flush()

    write_track_streams_laps(session, activity.id, parsed)

    return activity.id


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

async def run_once(interactive: bool = False) -> None:
    run_started_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    async with async_session() as session:
        user_id = await _get_the_user(session)
        # Read-only — deliberately not _get_or_create_sync_state here, since
        # this session is never committed. Row creation happens exactly
        # once, in _record_run_result at the end of the run.
        existing_state = await session.scalar(select(GarminSyncState).limit(1))
        prior_last_synced = existing_state.last_synced_at if existing_state else None

    start_dt = (
        (prior_last_synced - LOOKBACK_BUFFER)
        if prior_last_synced is not None
        else (run_started_at - INITIAL_BACKFILL)
    )

    try:
        client = _build_client(interactive)
        activities = client.get_activities_by_date(
            start_dt.date().isoformat(),
            run_started_at.date().isoformat(),
            sortorder="asc",
        )
    except Exception as exc:  # noqa: BLE001 — auth/listing failure: log, don't advance cursor
        message = _scrub_secrets(str(exc))[:1000]
        await _record_run_result(run_started_at, "failed", message, advance_cursor=False)
        print(f"Garmin sync FAILED during auth/listing: {message}")
        return

    sport_cache: dict[str, int] = {}
    counts = {"success": 0, "failed": 0, "duplicate": 0}

    for raw in activities:
        # activityId is typed as Optional in garminconnect's own schema for
        # this endpoint — an unguarded raw["activityId"] here would crash
        # run_once() entirely on one malformed record (unlike every other
        # per-activity failure, which is caught below), permanently
        # stalling the loop on the same bad record every interval.
        raw_id = raw.get("activityId")
        if raw_id is None:
            print(f"Skipping an activity with no activityId: {raw.get('activityName', '<unknown>')}")
            counts["failed"] += 1
            continue
        external_id = str(raw_id)

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
                        session, client, user_id, sport_cache, raw, run_started_at
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
        except Exception as exc:  # noqa: BLE001 — one bad activity must not stop the run
            message = _scrub_secrets(str(exc))[:1000]
            async with async_session() as session:
                session.add(
                    ImportEvent(
                        user_id=user_id,
                        source=SOURCE,
                        status="failed",
                        error_message=message,
                    )
                )
                await session.commit()
            counts["failed"] += 1
            print(f"FAILED {external_id}: {message}")

    if counts["failed"] > 0:
        # Some activities failed but listing itself succeeded — don't mark
        # this "success" (the sync-health banner would then never surface
        # it) and don't advance the cursor either: the failed activities
        # are still inside this window, so the next run retries them
        # instead of the lookback buffer eventually sliding past them and
        # losing them for good.
        await _record_run_result(
            run_started_at,
            "failed",
            f"{counts['failed']} of {len(activities)} activities failed to "
            f"import this run — see import_events for details",
            advance_cursor=False,
        )
    else:
        await _record_run_result(run_started_at, "success", None, advance_cursor=True)
    print(f"Garmin sync done: {counts}")


async def run_loop(interval_s: int) -> None:
    while True:
        try:
            await run_once(interactive=False)
        except Exception as exc:  # noqa: BLE001 — the loop itself must never die
            print(f"Garmin sync loop iteration crashed: {_scrub_secrets(str(exc))}")
        await asyncio.sleep(interval_s)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--login-only", action="store_true")
    group.add_argument("--once", action="store_true")
    group.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    if args.login_only:
        _build_client(interactive=True)
        print(f"Login successful, session saved to {TOKENSTORE_DEFAULT}")
    elif args.once:
        asyncio.run(run_once(interactive=False))
    else:
        interval_s = int(os.environ.get("GARMIN_SYNC_INTERVAL_S", "3600"))
        asyncio.run(run_loop(interval_s))


if __name__ == "__main__":
    main()
