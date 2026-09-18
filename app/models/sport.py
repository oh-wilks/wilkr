from sqlalchemy import CheckConstraint, Identity, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Sport(Base):
    __tablename__ = "sports"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        comment=(
            "Not a fixed enum — populated dynamically by importers from "
            "source activity-type strings (see sport_mapping.py, "
            "garmin_sport_mapping.py). Cycling sub-types diverge by source: "
            "Strava's bulk CSV export has no sub-type field, so all cycling "
            "collapses into one generic 'cycling' row; Garmin sync splits "
            "them (road_biking, mountain_biking, gravel_cycling, ...) since "
            "Garmin's API actually distinguishes them."
        ),
    )
    category: Mapped[str] = mapped_column(
        String, nullable=False, comment="endurance, strength, other"
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('endurance', 'strength', 'other')",
            name="ck_sports_category",
        ),
    )
