"""Garmin Connect activityType.typeKey -> (sport name, category).

Unlike sport_mapping.py (built from a real 825-activity Strava export),
this one is NOT verified against real Garmin sync data — there isn't any
yet. It's a best-effort mapping using Garmin's known, documented typeKey
vocabulary, aimed at reusing the same sport names the Strava import already
created (running, hike, walk, alpine_ski, nordic_ski, swim, kayak,
snowshoe, rowing, rollerblading, workout, water_sports) so historical and
live data line up under the same sport rows where the concepts genuinely
match.

Cycling sub-types are the deliberate exception: sport_mapping.py collapses
all of them into one generic "cycling" bucket because the Strava bulk CSV
export has no sub-type field to disambiguate — that data genuinely isn't
there. Garmin's API is not under that limitation (road_biking/
mountain_biking/gravel_cycling/etc. are real, distinct typeKeys it sends),
so collapsing them here too would be throwing away signal that actually
exists, purely to match a historical-data limitation on the *other*
importer. They're kept as their own sport rows instead. The trade-off:
segment matching filters on exact sport_id (app/segments/matching.py), so
this also splits segment matching per cycling sub-type going forward — a
segment from a future MTB ride won't match a future gravel ride on the
same physical trail, and neither matches any historical Strava-imported
"cycling" activity. Existing Strava-imported cycling data is unaffected
and unmergeable with these — it stays under the generic "cycling" row
permanently, since the CSV it came from never recorded which sub-type it
was.

Same safety net as sport_mapping.py: an unmapped typeKey falls back to a
slugified version of itself with category="other" rather than blocking a
sync, so a wrong or missing guess here is never a hard failure — worst
case is a new sport row that doesn't merge with an existing one, easily
fixed once real data shows what Garmin actually sends.
"""

GARMIN_TYPE_TO_SPORT: dict[str, tuple[str, str]] = {
    "running": ("running", "endurance"),
    "trail_running": ("running", "endurance"),
    "track_running": ("running", "endurance"),
    "treadmill_running": ("running", "endurance"),
    "street_running": ("running", "endurance"),
    "cycling": ("cycling", "endurance"),
    "road_biking": ("road_biking", "endurance"),
    "mountain_biking": ("mountain_biking", "endurance"),
    "gravel_cycling": ("gravel_cycling", "endurance"),
    "track_cycling": ("track_cycling", "endurance"),
    "indoor_cycling": ("indoor_cycling", "endurance"),
    "virtual_ride": ("virtual_ride", "endurance"),
    "cyclocross": ("cyclocross", "endurance"),
    "hiking": ("hike", "endurance"),
    "walking": ("walk", "endurance"),
    "casual_walking": ("walk", "endurance"),
    "speed_walking": ("walk", "endurance"),
    "swimming": ("swim", "endurance"),
    "lap_swimming": ("swim", "endurance"),
    "open_water_swimming": ("swim", "endurance"),
    "resort_skiing_snowboarding_ws": ("alpine_ski", "endurance"),
    "resort_skiing": ("alpine_ski", "endurance"),
    "downhill_skiing": ("alpine_ski", "endurance"),
    "snowboarding": ("alpine_ski", "endurance"),
    "cross_country_skiing_ws": ("nordic_ski", "endurance"),
    "cross_country_skiing": ("nordic_ski", "endurance"),
    "backcountry_skiing_snowboarding_ws": ("nordic_ski", "endurance"),
    "skate_skiing_ws": ("nordic_ski", "endurance"),
    "snowshoeing": ("snowshoe", "endurance"),
    "rowing": ("rowing", "endurance"),
    "indoor_rowing": ("rowing", "endurance"),
    "kayaking": ("kayak", "endurance"),
    "kayaking_v2": ("kayak", "endurance"),
    "stand_up_paddleboarding": ("water_sports", "other"),
    "whitewater_rafting_kayaking": ("water_sports", "other"),
    "fitness_equipment": ("workout", "other"),
    "cardio_training": ("workout", "other"),
    "indoor_cardio": ("workout", "other"),
    "elliptical": ("workout", "other"),
    "strength_training": ("workout", "other"),
    "yoga": ("workout", "other"),
    "pilates": ("workout", "other"),
    "inline_skating": ("rollerblading", "endurance"),
}

DEFAULT_CATEGORY = "other"


def sport_slug(raw_type_key: str) -> str:
    return raw_type_key.strip().lower().replace(" ", "_")


def resolve_sport(raw_type_key: str) -> tuple[str, str]:
    if raw_type_key in GARMIN_TYPE_TO_SPORT:
        return GARMIN_TYPE_TO_SPORT[raw_type_key]
    return sport_slug(raw_type_key), DEFAULT_CATEGORY
