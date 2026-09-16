from __future__ import annotations

import gzip
import pathlib
import tempfile

from tcxreader.tcxreader import TCXReader

from app.importers.parsers.common import LapData, ParsedActivity, TrackPoint


def parse_tcx(path: pathlib.Path) -> ParsedActivity:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as fh:
        content = fh.read()

    # Strava's TCX export has leading whitespace before the <?xml ...?>
    # declaration, which is invalid XML and breaks a direct parse — strip it
    # via a temp file rather than feeding the raw path to tcxreader.
    content = content.lstrip()

    reader = TCXReader()
    with tempfile.NamedTemporaryFile(suffix=".tcx") as tmp:
        tmp.write(content)
        tmp.flush()
        # only_gps=False: many activities in this account are indoor/trainer
        # sessions with cadence/power but no GPS at all. The default
        # only_gps=True silently drops every trackpoint in that case (it
        # trims points without GPS from the start/end, which is all of them
        # when none have GPS) — we still want the streams and laps.
        data = reader.read(tmp.name, only_gps=False)

    result = ParsedActivity()
    for tp in data.trackpoints:
        result.points.append(
            TrackPoint(
                time=tp.time,
                lat=tp.latitude,
                lon=tp.longitude,
                ele=tp.elevation,
                hr=int(tp.hr_value) if tp.hr_value is not None else None,
                cadence=int(tp.cadence) if tp.cadence is not None else None,
                power=int(tp.tpx_ext["Watts"]) if tp.tpx_ext and "Watts" in tp.tpx_ext else None,
            )
        )

    for idx, lap in enumerate(data.laps, start=1):
        ascent = lap.ascent
        descent = lap.descent
        result.laps.append(
            LapData(
                lap_index=idx,
                started_at=lap.start_time,
                elapsed_time_s=int(lap.duration) if lap.duration is not None else 0,
                distance_m=lap.distance,
                elevation_change_m=(
                    (ascent or 0) - (descent or 0)
                    if ascent is not None or descent is not None
                    else None
                ),
                avg_speed_mps=lap.avg_speed,
                max_speed_mps=lap.max_speed,
            )
        )

    return result
