from __future__ import annotations

import gzip
import pathlib

import fitdecode

from app.importers.parsers.common import LapData, ParsedActivity, TrackPoint


def _semicircles_to_degrees(value: int) -> float:
    return value * (180.0 / 2**31)


def _first(frame: fitdecode.FitDataMessage, *names: str):
    for name in names:
        value = frame.get_value(name, fallback=None)
        if value is not None:
            return value
    return None


def parse_fit(path: pathlib.Path) -> ParsedActivity:
    opener = gzip.open if path.suffix == ".gz" else open
    result = ParsedActivity()
    lap_index = 0

    with opener(path, "rb") as fh, fitdecode.FitReader(fh) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue

            if frame.name == "record":
                timestamp = frame.get_value("timestamp", fallback=None)
                if timestamp is None:
                    continue
                lat_raw = frame.get_value("position_lat", fallback=None)
                lon_raw = frame.get_value("position_long", fallback=None)
                result.points.append(
                    TrackPoint(
                        time=timestamp,
                        lat=_semicircles_to_degrees(lat_raw) if lat_raw is not None else None,
                        lon=_semicircles_to_degrees(lon_raw) if lon_raw is not None else None,
                        ele=_first(frame, "enhanced_altitude", "altitude"),
                        hr=frame.get_value("heart_rate", fallback=None),
                        cadence=frame.get_value("cadence", fallback=None),
                        power=frame.get_value("power", fallback=None),
                        speed=_first(frame, "enhanced_speed", "speed"),
                    )
                )

            elif frame.name == "lap":
                start_time = frame.get_value("start_time", fallback=None)
                elapsed = frame.get_value("total_elapsed_time", fallback=None)
                if start_time is None or elapsed is None:
                    continue
                lap_index += 1
                ascent = frame.get_value("total_ascent", fallback=None)
                descent = frame.get_value("total_descent", fallback=None)
                result.laps.append(
                    LapData(
                        lap_index=lap_index,
                        started_at=start_time,
                        elapsed_time_s=int(elapsed),
                        distance_m=frame.get_value("total_distance", fallback=None),
                        elevation_change_m=(
                            (ascent or 0) - (descent or 0)
                            if ascent is not None or descent is not None
                            else None
                        ),
                        avg_speed_mps=_first(frame, "enhanced_avg_speed", "avg_speed"),
                        max_speed_mps=_first(frame, "enhanced_max_speed", "max_speed"),
                    )
                )

    return result
