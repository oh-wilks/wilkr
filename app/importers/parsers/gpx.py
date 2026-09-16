from __future__ import annotations

import gzip
import pathlib

import gpxpy

from app.importers.parsers.common import ParsedActivity, TrackPoint


def _extension_value(extensions, tag_suffix: str) -> str | None:
    """Best-effort lookup into <extensions> children (e.g. Garmin's
    TrackPointExtension: hr/cad/atemp) — GPX has no fixed schema for this,
    so plenty of files just won't have it, which is fine."""
    for ext in extensions:
        if ext.tag.endswith(tag_suffix):
            return ext.text
        for child in ext:
            if child.tag.endswith(tag_suffix):
                return child.text
    return None


def parse_gpx(path: pathlib.Path) -> ParsedActivity:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        gpx = gpxpy.parse(fh)

    result = ParsedActivity()
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                hr = _extension_value(point.extensions, "hr")
                cadence = _extension_value(point.extensions, "cad")
                result.points.append(
                    TrackPoint(
                        time=point.time,
                        lat=point.latitude,
                        lon=point.longitude,
                        ele=point.elevation,
                        hr=int(hr) if hr is not None else None,
                        cadence=int(cadence) if cadence is not None else None,
                    )
                )

    # GPX has no lap markers — result.laps stays empty.
    return result
