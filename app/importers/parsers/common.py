from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass
class TrackPoint:
    time: datetime.datetime
    lat: float | None = None
    lon: float | None = None
    ele: float | None = None
    hr: int | None = None
    cadence: int | None = None
    power: int | None = None
    speed: float | None = None


@dataclass
class LapData:
    lap_index: int
    started_at: datetime.datetime
    elapsed_time_s: int
    distance_m: float | None = None
    elevation_change_m: float | None = None
    avg_speed_mps: float | None = None
    max_speed_mps: float | None = None


@dataclass
class ParsedActivity:
    """Common shape every format-specific parser produces.

    lat/lon are optional on TrackPoint — indoor/trainer activities (common in
    this account's .tcx files) have cadence/power/HR but no GPS at all, so a
    parsed activity may have points with no track geometry to build.
    """

    points: list[TrackPoint] = field(default_factory=list)
    laps: list[LapData] = field(default_factory=list)

    @property
    def has_gps(self) -> bool:
        return any(p.lat is not None and p.lon is not None for p in self.points)
