"""Strava's "Type d'activité" (French-locale export) -> (sport name, category).

Built from the actual distinct values in a real export archive (841
activities), not guessed. "Vélo" (cycling) is intentionally a single
generic bucket rather than split into road_cycling/mtb — the bulk CSV
export has no sub-type field to disambiguate them (that granularity only
exists via Strava's live API), and equipment-name heuristics are too
fragile to trust for a permanent classification.

Anything not in this dict falls back to a slugified version of the raw
Strava string with category="other" (see sport_slug/DEFAULT_CATEGORY below)
so an unrecognized future activity type can never block the import.
"""

STRAVA_TYPE_TO_SPORT: dict[str, tuple[str, str]] = {
    "Vélo": ("cycling", "endurance"),
    "Course à pied": ("running", "endurance"),
    "Entraînement": ("workout", "other"),
    "Ski nordique": ("nordic_ski", "endurance"),
    "Randonnée": ("hike", "endurance"),
    "Crossfit": ("crossfit", "strength"),
    "Ski alpin": ("alpine_ski", "endurance"),
    "Natation": ("swim", "endurance"),
    "Kayak": ("kayak", "endurance"),
    "Marche": ("walk", "endurance"),
    "Raquettes": ("snowshoe", "endurance"),
    "Aviron": ("rowing", "endurance"),
    "Sports nautiques": ("water_sports", "other"),
    "Roller": ("rollerblading", "endurance"),
}

DEFAULT_CATEGORY = "other"


def sport_slug(raw_strava_type: str) -> str:
    """Fallback sport name for an unmapped Strava type: lowercase, spaces
    to underscores. Good enough as a stable, readable key — no accent
    stripping or further normalization since this only exists as a
    safety net, not the primary path."""
    return raw_strava_type.strip().lower().replace(" ", "_")


def resolve_sport(raw_strava_type: str) -> tuple[str, str]:
    if raw_strava_type in STRAVA_TYPE_TO_SPORT:
        return STRAVA_TYPE_TO_SPORT[raw_strava_type]
    return sport_slug(raw_strava_type), DEFAULT_CATEGORY
