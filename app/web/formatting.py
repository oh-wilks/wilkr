"""Jinja2 filters and small helpers for the web viewer.

Display-layer only — matches the units convention (store metric always,
convert/format only here). No imperial conversion yet: that needs
user_preferences.unit_system to actually be settable from somewhere, and
there's no settings page yet — always metric for now.
"""

from __future__ import annotations

import datetime


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_distance(meters: float | None) -> str:
    if meters is None:
        return "—"
    return f"{meters / 1000:.1f} km"


def format_elevation(meters: float | None) -> str:
    if meters is None:
        return "—"
    return f"{meters:.0f} m"


# Sports where pace (min:sec per km) is the natural unit rather than
# speed — foot- and glide-based endurance sports where "how long per km"
# is what people actually think in. Everything else (cycling in all its
# sub-types, alpine skiing, etc.) defaults to km/h. "running" alone covers
# trail running too: both importers fold every running variant (trail,
# track, treadmill, street) into that one sport name — see
# garmin_sport_mapping.py / sport_mapping.py.
PACE_SPORTS = {"running", "hike", "walk", "nordic_ski"}

# A near-stopped GPS point (traffic light, photo stop) divides down to an
# enormous or infinite pace — cap the display rather than let one blip
# dominate a chart's whole y-axis scale.
_PACE_CAP_SECS_PER_KM = 1200  # 20:00/km


def format_speed(mps: float | None, sport_name: str | None = None) -> str:
    if mps is None:
        return "—"
    if sport_name in PACE_SPORTS:
        return format_pace(mps)
    return f"{mps * 3.6:.1f} km/h"


def format_pace(mps: float | None) -> str:
    if mps is None or mps <= 0.1:
        return "—"
    secs_per_km = min(1000.0 / mps, _PACE_CAP_SECS_PER_KM)
    minutes, seconds = divmod(round(secs_per_km), 60)
    return f"{minutes}:{seconds:02d} /km"


def speed_stream_for_display(
    points: list[dict], sport_name: str | None
) -> tuple[list[dict], str]:
    """Converts a raw speed stream (m/s, as stored) into display units for
    charting — km/h normally, or seconds-per-km (capped, see
    _PACE_CAP_SECS_PER_KM) for PACE_SPORTS, left for the chart's own tick
    formatter to render as MM:SS. Returns (points, mode) so the caller
    knows which chart label/formatter to wire up."""
    if sport_name in PACE_SPORTS:
        converted = [
            {**p, "v": min(1000.0 / p["v"], _PACE_CAP_SECS_PER_KM) if p["v"] and p["v"] > 0.1 else _PACE_CAP_SECS_PER_KM}
            for p in points
        ]
        return converted, "pace"
    return [{**p, "v": p["v"] * 3.6} for p in points], "kmh"


def format_grade(pct: float | None) -> str:
    if pct is None:
        return "—"
    return f"{pct:+.1f}%"


def format_date(value: datetime.datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d %b %Y, %H:%M")


def format_date_short(value: datetime.datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d %b %Y")


def format_time_ago(value: datetime.datetime | None) -> str:
    if value is None:
        return "never"
    delta = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def format_rank(rank: int, total: int) -> str:
    if rank == 1:
        return "PR"
    return f"{_ordinal(rank)} best of {total}"


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def decimate(points: list, max_points: int = 500) -> list:
    """Even-stride downsampling — some FIT-derived streams have 5000+
    points, which is more resolution than a chart at typical widths can
    show and just bloats the page's inline JSON for no visible benefit."""
    n = len(points)
    if n <= max_points:
        return points
    stride = n / max_points
    return [points[int(i * stride)] for i in range(max_points)]
