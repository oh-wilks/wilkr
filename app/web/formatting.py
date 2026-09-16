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


def format_speed(mps: float | None) -> str:
    if mps is None:
        return "—"
    return f"{mps:.1f} m/s"


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


def decimate(points: list, max_points: int = 500) -> list:
    """Even-stride downsampling — some FIT-derived streams have 5000+
    points, which is more resolution than a chart at typical widths can
    show and just bloats the page's inline JSON for no visible benefit."""
    n = len(points)
    if n <= max_points:
        return points
    stride = n / max_points
    return [points[int(i * stride)] for i in range(max_points)]
