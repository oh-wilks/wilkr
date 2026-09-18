"""sports.name: update comment to reflect dynamic, source-dependent values

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-17

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_OLD_COMMENT = "running, road_cycling, mtb, alpine_ski, nordic_ski, hike, swim"
_NEW_COMMENT = (
    "Not a fixed enum — populated dynamically by importers from source "
    "activity-type strings (see sport_mapping.py, garmin_sport_mapping.py). "
    "Cycling sub-types diverge by source: Strava's bulk CSV export has no "
    "sub-type field, so all cycling collapses into one generic 'cycling' "
    "row; Garmin sync splits them (road_biking, mountain_biking, "
    "gravel_cycling, ...) since Garmin's API actually distinguishes them."
)


def upgrade() -> None:
    op.alter_column(
        "sports",
        "name",
        comment=_NEW_COMMENT,
        existing_comment=_OLD_COMMENT,
    )


def downgrade() -> None:
    op.alter_column(
        "sports",
        "name",
        comment=_OLD_COMMENT,
        existing_comment=_NEW_COMMENT,
    )
